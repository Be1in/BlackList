"""Core event set reconstruction over the whole study range (ticket 05).

The representative sample (ticket 04) proved the method on six slots. This module
runs the same reconstruction over *every* retrievable event inside the observation
window fixed by ``config/study_scope.json`` and emits one core event table, the
balance table, the validation table and the chain registry -- plus the coverage
and exclusion records that make the denominator auditable.

Three things are added at this scale, and only these three:

* **Full-range retrieval coverage.** One independent provider's ``eth_getLogs``
  over ``[observation_start_block, study_cutoff_block]`` gives the expected log
  set. Positions are compared by ``(tx_hash, log_index)``, so the coverage
  denominator is external to the snapshot rather than assumed from it.
* **Verification that scales with evidence.** Positions come from that same
  independent set instead of one receipt per event; contract versions are checked
  once per distinct code hash (code is constant between upgrades); balances are
  re-derived per event from an independent archive endpoint.
* **A fixed exclusion function.** R1/R7/R8 exclude a record and write the rule
  id plus its evidence on the row; R2/R3/R4 keep the record and only downgrade
  its eligibility or confidence; R9 is an attribution rule -- those rows stay in
  the core set, are counted in ``core_set.r9_attribution_rows`` and are
  identifiable by ``blacklist_transition = false``. Matching never filters: the
  OFAC string check is recorded per event but the eligibility function does not
  read it, and the manifest states ``match_based_exclusions = 0``.

Nothing here re-implements parsing: every event row still comes from
``Reconstruction`` (ticket 02/03), so the verification boundary stays at the
reconstruction output.
"""

from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from . import contracts
from .config import Config
from .lifecycle import merge_bundles, validation_row
from .pipeline import (
    Reconstruction,
    blacklist_state_label,
    build_block_events,
    load_ofac_index,
    load_snapshot_document,
    token_amount,
)
from .rpc import RpcCache, RpcError, canonical_json, now_utc, sha256_hex
from .tables import (
    assert_columns,
    artifact_digest,
    data_digest,
    load_dictionary,
    ratio,
    write_bundle,
    write_csv,
)

DECISION_PATH = "config/study_scope.json"
EXCLUSIONS_PATH = "data/derived/exclusions.csv"
TABLE_ORDER = ("events", "balances", "validation", "chain_registry")
LOG_EXCLUDED_FIELDS = ("block_time_utc", "blacklist_state_before", "blacklist_state_after")

# One bad event must not stop the run, but a programming error must not be
# disguised as "the endpoint was unavailable" either.
RECONSTRUCTION_ERRORS = (RpcError, SystemExit, KeyError, TypeError, ValueError, IndexError)


def core_id_for(
    cfg: Config,
    snapshot_sha: str,
    decision_sha: str,
    start_block: int,
    cutoff_block: int,
) -> str:
    """Bundle id derived from the inputs alone, so ticket 06 can locate it without a run."""
    dictionary_sha = sha256_hex(cfg.dictionary_csv.read_bytes())
    return "core_" + sha256_hex(
        canonical_json(
            {
                "log_snapshot_sha256": snapshot_sha,
                "decision_sha256": decision_sha,
                "observation_start_block": start_block,
                "study_cutoff_block": cutoff_block,
                "dictionary_sha256": dictionary_sha,
                "dataset_contract_version": cfg.registry["dataset_contract_version"],
            }
        ).encode()
    )[:32]


def locate_core_bundle(
    cfg: Config, core_id: str = "", *, require_current_inputs: bool = False
) -> tuple[Path, dict]:
    """Locate the ticket-05 bundle from its inputs, never from "the newest directory".

    Downstream layers (matching, issuer comparison) must read the bundle whose id is
    derived from the same snapshots and decision; guessing a directory would let them
    read a batch built from different inputs. ``require_current_inputs`` makes an
    explicitly supplied id fail when it was built from other inputs -- the comparison
    layer needs that, while the match layer keeps its documented ``--core-id`` escape.
    """
    decision_raw = cfg.path(DECISION_PATH).read_bytes()
    decision = json.loads(decision_raw.decode("utf-8"))
    derived = core_id_for(
        cfg,
        sha256_hex(cfg.log_snapshot.read_bytes()),
        sha256_hex(decision_raw),
        int(decision["observation_start_block"]),
        int(decision["study_cutoff_block"]),
    )
    resolved = core_id or derived
    out_dir = cfg.out_root / resolved
    manifest_path = out_dir / "manifest.json"
    if not manifest_path.exists():
        raise SystemExit(
            f"核心集产物缺失：{cfg.rel(manifest_path)}；"
            "先运行 python scripts/reconstruct_core.py"
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("core_id") != resolved:
        raise SystemExit(
            f"{cfg.rel(manifest_path)} 的 core_id={manifest.get('core_id')} "
            f"与输入派生的 {resolved} 不一致"
        )
    if require_current_inputs and resolved != derived:
        raise SystemExit(
            f"核心集 {resolved} 不是由当前日志快照/决策/字典生成的（应为 {derived}）；"
            "先重跑核心集再比较"
        )
    return out_dir, manifest


def _is_int(value: object) -> bool:
    text = str(value).strip()
    return text.isdigit() or (text.startswith("-") and text[1:].isdigit())


def _log_key(log: dict) -> tuple[str, int]:
    return log["transactionHash"].lower(), int(log["logIndex"], 16)


def _position(log: dict) -> str:
    return (
        f"{int(log['blockNumber'], 16):010d}:"
        f"{int(log['transactionIndex'], 16):04d}:{int(log['logIndex'], 16):06d}"
    )


def _event_id(chain: str, log: dict) -> str:
    return "ev_" + sha256_hex(
        f"{chain}|{log['transactionHash'].lower()}|{int(log['logIndex'], 16)}".encode()
    )[:32]


def _snapshot_query(snapshot: dict, registry: dict, topics: dict) -> str:
    tokens = [
        contracts.contract_for_address(registry, address) or address
        for address in snapshot["addresses"]
    ]
    signatures = [topics[topic.lower()].signature for topic in snapshot["topics"]]
    return (
        f"eth_getLogs(address=[{','.join(tokens)}]"
        f", fromBlock={snapshot['start_block']}, toBlock={snapshot['end_block']}"
        f", topics=[{','.join(signatures)}])"
    )


@dataclass
class ReferenceLogSet:
    """Independent full-range log retrieval used as the coverage denominator."""

    logs: list[dict]
    retrieved_at_utc: str
    provider_labels: list[str]
    complete: bool
    errors: list[str]

    def positions(self) -> dict[tuple[str, int], str]:
        return {_log_key(log): _position(log) for log in self.logs}


class RangeLogRetriever:
    """``eth_getLogs`` over the study range, split until each chunk fits the cap."""

    MAX_DEPTH = 26

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.rpc = RpcCache(cfg)

    def fetch(self, start_block: int, cutoff_block: int) -> ReferenceLogSet:
        logs: list[dict] = []
        errors: list[str] = []
        labels: set[str] = set()
        retrieved: list[str] = []

        for token in self.cfg.token_order:
            address = self.cfg.tokens[token]["contract"].lower()
            for spec in self.cfg.tokens[token]["events"].values():
                first = len(logs)
                self._fetch_range(
                    address,
                    spec["topic0"],
                    start_block,
                    cutoff_block,
                    0,
                    logs,
                    errors,
                    labels,
                    retrieved,
                )
                self._repair_positions(
                    logs, first, address, spec["topic0"], errors, labels, retrieved
                )

        seen: set[tuple[str, int]] = set()
        unique: list[dict] = []
        duplicates = 0
        for log in logs:
            try:
                key = _log_key(log)
            except (KeyError, TypeError, ValueError):
                errors.append(
                    f"独立检索返回无法定位位置的日志：{log.get('transactionHash')}"
                )
                continue
            if key in seen:
                duplicates += 1
                continue
            seen.add(key)
            unique.append(log)
        if duplicates:
            errors.append(f"独立检索返回 {duplicates} 条重复日志（已去重）")
        unique.sort(
            key=lambda log: (
                int(log["blockNumber"], 16),
                int(log["transactionIndex"], 16),
                int(log["logIndex"], 16),
            )
        )
        return ReferenceLogSet(
            logs=unique,
            retrieved_at_utc=max(retrieved, default=""),
            provider_labels=sorted(labels),
            complete=not errors,
            errors=errors,
        )

    def _fetch_range(
        self,
        address: str,
        topic0: str,
        low: int,
        high: int,
        depth: int,
        out: list[dict],
        errors: list[str],
        labels: set[str],
        retrieved: list[str],
    ) -> None:
        params = [
            {
                "address": address,
                "fromBlock": hex(low),
                "toBlock": hex(high),
                "topics": [topic0],
            }
        ]
        try:
            probe = self.rpc.probe("eth_getLogs", params, provider="reference")
        except RpcError as exc:
            message = str(exc)
            if low < high and depth < self.MAX_DEPTH and "分页截断" in message:
                middle = (low + high) // 2
                self._fetch_range(
                    address, topic0, low, middle, depth + 1, out, errors, labels, retrieved
                )
                self._fetch_range(
                    address,
                    topic0,
                    middle + 1,
                    high,
                    depth + 1,
                    out,
                    errors,
                    labels,
                    retrieved,
                )
                return
            errors.append(f"{topic0} [{low}, {high}]: {message}")
            return

        labels.add(probe.provider_label)
        retrieved.append(probe.retrieved_at_utc)
        out.extend(probe.result)

    def _repair_positions(
        self,
        logs: list[dict],
        first: int,
        address: str,
        topic0: str,
        errors: list[str],
        labels: set[str],
        retrieved: list[str],
    ) -> None:
        """Replace blocks whose log positions are unusable with a block-level query.

        Etherscan's log API returns ``transactionIndex`` (and occasionally
        ``logIndex``) as ``0x`` for some blocks. A position that is guessed is
        worse than no position, so every affected block is re-read from an
        archive JSON-RPC endpoint that returns the full quantity set.
        """
        slice_ = logs[first:]
        bad_blocks: set[int] = set()
        for log in slice_:
            try:
                int(log["blockNumber"], 16)
                int(log["transactionIndex"], 16)
                int(log["logIndex"], 16)
            except (KeyError, TypeError, ValueError):
                try:
                    bad_blocks.add(int(log["blockNumber"], 16))
                except (KeyError, TypeError, ValueError):
                    errors.append(f"{topic0}: 日志缺少可用区块号，无法定位 {log.get('transactionHash')}")
        if not bad_blocks:
            return

        # 先取到替代日志，再替换原区块：替代端点失败时保留原记录，由 fetch()
        # 的错误路径显式丢弃并把 complete 置 false，不让分母静默缩小。
        replacements: dict[int, list[dict]] = {}
        for block in sorted(bad_blocks):
            params = [
                {
                    "address": address,
                    "fromBlock": hex(block),
                    "toBlock": hex(block),
                    "topics": [topic0],
                }
            ]
            try:
                probe = self.rpc.probe(
                    "eth_getLogs", params, provider="reference_archive"
                )
            except RpcError as exc:
                errors.append(f"{topic0} 区块 {block} 位置不可用且替代端点失败：{exc}")
                continue
            labels.add(probe.provider_label)
            retrieved.append(probe.retrieved_at_utc)
            good: list[dict] = []
            for log in probe.result:
                try:
                    int(log["logIndex"], 16)
                    int(log["transactionIndex"], 16)
                except (KeyError, TypeError, ValueError):
                    errors.append(
                        f"{topic0} 区块 {block} 的替代日志仍缺位置："
                        f"{log.get('transactionHash')}"
                    )
                    continue
                good.append(log)
            replacements[block] = good

        kept: list[dict] = []
        for log in slice_:
            try:
                block = int(log["blockNumber"], 16)
            except (KeyError, TypeError, ValueError):
                kept.append(log)
                continue
            if block in replacements:
                continue
            kept.append(log)
        del logs[first:]
        logs.extend(kept)
        for block in sorted(replacements):
            logs.extend(replacements[block])


@dataclass
class CoreRun:
    cfg: Config
    start_block: int
    cutoff_block: int
    decision_path: str = DECISION_PATH
    workers: int = 8
    max_events: int | None = None
    dictionary: dict[str, list[str]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    note_counts: Counter = field(default_factory=Counter)
    deviations: list[dict] = field(default_factory=list)
    balance_errors: dict[str, dict] = field(default_factory=dict)
    non_comparable_checks: int = 0
    prewindow_cutoff_added: int = 0
    cache_stats: dict[str, int] = field(
        default_factory=lambda: {"state_calls_from_cache": 0, "probes_from_cache": 0}
    )

    def __post_init__(self) -> None:
        self.rpc = RpcCache(self.cfg)
        self.topics = contracts.verify_topic_table(self.cfg.registry)
        self.dictionary = load_dictionary(self.cfg.dictionary_csv)
        self.snapshot, self.snapshot_sha = load_snapshot_document(self.cfg.log_snapshot)
        self.block_events = build_block_events(
            self.snapshot, self.cfg.registry, self.topics
        )
        self.ofac_index, self.ofac_manifest = load_ofac_index(self.cfg)

    # ---------------------------------------------------------------- decision
    def _load_decision(self) -> tuple[dict, str]:
        path = self.cfg.path(self.decision_path)
        raw = path.read_bytes()
        decision = json.loads(raw.decode("utf-8"))
        chains = decision.get("scope", {}).get("chains", [])
        if chains != [self.cfg.chain["name"]]:
            raise SystemExit(f"研究范围声明的链 {chains} 与采集链不一致")
        tokens = decision.get("scope", {}).get("tokens", [])
        if tokens != list(self.cfg.token_order):
            raise SystemExit(f"研究范围声明的代币 {tokens} 与登记的代币不一致")
        return decision, sha256_hex(raw)

    # --------------------------------------------------------------- enumerate
    def _in_range(self, log: dict) -> bool:
        block = int(log["blockNumber"], 16)
        return self.start_block <= block <= self.cutoff_block

    def _logs(self) -> tuple[list[dict], list[dict]]:
        inside: list[dict] = []
        outside: list[dict] = []
        for log in self.snapshot["logs"]:
            (inside if self._in_range(log) else outside).append(log)
        inside.sort(
            key=lambda log: (
                int(log["blockNumber"], 16),
                int(log["transactionIndex"], 16),
                int(log["logIndex"], 16),
            )
        )
        outside.sort(
            key=lambda log: (
                int(log["blockNumber"], 16),
                int(log["transactionIndex"], 16),
                int(log["logIndex"], 16),
            )
        )
        return inside, outside

    # ------------------------------------------------------------- reconstruct
    def _reconstruct(
        self, log: dict, reference_positions: dict[tuple[str, int], str], reference_at: str
    ) -> dict:
        reconstruction = Reconstruction(
            cfg=self.cfg,
            tx_hash=log["transactionHash"],
            log_index=int(log["logIndex"], 16),
            cutoff_block=self.cutoff_block,
            reference_positions=reference_positions,
            reference_retrieved_at=reference_at,
            reference_state_provider="reference_archive",
            verify_contract_version=False,
            emit_schema_rows=False,
            ofac_index=self.ofac_index,
            block_events=self.block_events,
        )
        outcome = reconstruction.run()
        return {
            "outcome": outcome,
            "notes": reconstruction.notes,
            "deviations": reconstruction.deviations,
            "cache_stats": reconstruction.cache_stats,
        }

    def _fallback(self, log: dict, error: Exception) -> dict:
        """A snapshot log whose auxiliary evidence could not be fetched.

        R2/R3: the event stays in the core set with log-derived fields only, the
        missing fields are named, and the reason is recorded on the row. It is not
        silently dropped and not replaced by a zero.
        """
        row = {field: "" for field in self.dictionary["events"]}
        topic0 = log["topics"][0].lower()
        spec = self.topics.get(topic0)
        token = contracts.contract_for_address(self.cfg.registry, log["address"])
        if spec is None or token is None:
            raise SystemExit(f"日志无法映射到已登记事件: {topic0} / {log['address']}")
        affected, decode_source, destruction_raw = contracts.decode_event_log(log, spec)
        block = int(log["blockNumber"], 16)
        log_index = int(log["logIndex"], 16)
        tx = log["transactionHash"].lower()
        tx_count = sum(
            1
            for other in self.snapshot["logs"]
            if other["transactionHash"].lower() == tx
        )
        decimals = self.cfg.tokens[token]["decimals"]
        abi_path = self.cfg.path(self.cfg.tokens[token]["abi_snapshot"])
        abi_sha = sha256_hex(abi_path.read_bytes())

        row.update(
            {
                "event_id": _event_id(self.cfg.chain["name"], log),
                "chain": self.cfg.chain["name"],
                "chain_id": str(self.cfg.chain["chain_id"]),
                "token_symbol": token,
                "token_contract": log["address"].lower(),
                "block_number": str(block),
                "block_hash": log["blockHash"].lower(),
                "event_time_precision": "second",
                "tx_hash": tx,
                "tx_index": str(int(log["transactionIndex"], 16)),
                "log_index": str(log_index),
                "log_order_key": _position(log),
                "tx_event_count": str(tx_count),
                "event_topic0": topic0,
                "event_signature": spec.signature,
                "abi_source": contracts.abi_source(abi_sha, self.cfg.rel(abi_path), ""),
                "raw_topics": canonical_json(log["topics"]),
                "raw_data": log["data"],
                "event_class": spec.event_class,
                "affected_address": affected,
                "address_decode_source": decode_source,
                "destruction_amount_raw": destruction_raw or "",
                "destruction_amount_token": (
                    token_amount(destruction_raw, decimals) if destruction_raw else ""
                ),
                "token_decimals": str(decimals),
                "balance_pre_event_state_block": str(block - 1),
                "balance_pre_event_source": "archive_state_end_of_block",
                "balance_pre_event_precision": "lower_bound_only",
                "source_endpoint_label": self.cfg.primary.label,
                "source_query": _snapshot_query(
                    self.snapshot, self.cfg.registry, self.topics
                ),
                "source_retrieved_at_utc": self.snapshot["retrieved_at_utc"],
                "raw_log_path": self.cfg.rel(self.cfg.log_snapshot),
                "raw_log_sha256": self.snapshot_sha,
                "dataset_contract_version": self.cfg.registry["dataset_contract_version"],
                "contract_version_confidence": "low",
                "missingness_status": "partial",
                "missing_fields": ";".join(
                    [
                        "block_time_utc",
                        "contract_code_hash",
                        "blacklist_state_before",
                        "blacklist_state_after",
                        "balance_pre_event_raw",
                    ]
                ),
                # 历史状态失败与普通 RPC 失败分开记：字典要求原因码指向真正的缺口。
                "missing_reason_code": (
                    "archive_state_unavailable"
                    if "eth_call" in str(error).lower() or "archive" in str(error).lower()
                    else "rpc_unavailable"
                ),
                "verification_status": "unverifiable",
                "analysis_eligibility": "core_unverifiable",
                "notes": f"reconstruction failed: {type(error).__name__}: {error}",
            }
        )
        validation = [
            validation_row(
                "source_position",
                "log_order_key",
                row["log_order_key"],
                "reference_unavailable",
                "重建失败，未取得独立端点位置",
                event_id=row["event_id"],
                deviation="reference_missing",
                reason="external_source_unavailable",
                resolution="R2/R3：保留事件行，缺失字段逐项登记，不写零值",
                agreement="unverifiable",
            )
        ]
        return {"events": [row], "balances": [], "validation": validation}

    def _run_events(
        self, logs: list[dict], reference_positions: dict[tuple[str, int], str], reference_at: str
    ) -> tuple[list[dict], list[dict]]:
        outcomes: list[dict | None] = [None] * len(logs)
        failures: list[tuple[int, Exception]] = []
        total = len(logs)
        with ThreadPoolExecutor(max_workers=max(1, self.workers)) as pool:
            futures = {
                pool.submit(self._reconstruct, log, reference_positions, reference_at): index
                for index, log in enumerate(logs)
            }
            done = 0
            for future in as_completed(futures):
                index = futures[future]
                done += 1
                try:
                    outcomes[index] = future.result()
                except RECONSTRUCTION_ERRORS as exc:
                    failures.append((index, exc))
                if done % 250 == 0 or done == total:
                    print(
                        f"  reconstructed {done}/{total} events"
                        f"（失败 {len(failures)}）",
                        file=sys.stderr,
                        flush=True,
                    )

        for index, exc in failures:
            log = logs[index]
            outcomes[index] = {
                "outcome": self._fallback(log, exc),
                "notes": [],
                "deviations": [
                    {
                        "event": f"{_log_key(log)[0]}:{_log_key(log)[1]}",
                        "failure": f"{type(exc).__name__}: {exc}",
                        "rule": "R2/R3",
                    }
                ],
                "cache_stats": {},
            }

        ordered = [outcome for outcome in outcomes if outcome is not None]
        for entry in ordered:
            # 逐事件说明可能重复上千次（如"D2 地址已不在黑名单"），按文本计数，
            # 避免 manifest 被同一条说明淹没。
            self.note_counts.update(entry["notes"])
            self.deviations.extend(entry["deviations"])
            for key, value in entry["cache_stats"].items():
                self.cache_stats[key] += value
        return [entry["outcome"] for entry in ordered]

    # ------------------------------------------------------- coverage records
    def _coverage(
        self,
        snapshot_logs: list[dict],
        reference: ReferenceLogSet,
    ) -> tuple[dict, list[dict], list[dict]]:
        reference_index = {
            _log_key(log): log for log in reference.logs
        }
        snapshot_index = {_log_key(log): log for log in snapshot_logs}

        missing_from_snapshot = sorted(set(reference_index) - set(snapshot_index))
        snapshot_only = sorted(set(snapshot_index) - set(reference_index))
        matched = set(reference_index) & set(snapshot_index)

        def bucket() -> dict:
            return {
                "expected_events": 0,
                "retrieved_events": 0,
                "matched_events": 0,
                "snapshot_only_events": 0,
                "reference_only_events": 0,
            }

        by_token: dict[str, dict] = {}
        by_class: dict[str, dict] = {}
        blocks: dict[int, dict[str, set]] = {}
        for key, log in reference_index.items():
            token = contracts.contract_for_address(self.cfg.registry, log["address"]) or "unknown"
            spec = self.topics.get(log["topics"][0].lower())
            event_class = spec.event_class if spec else "unknown"
            block = int(log["blockNumber"], 16)
            state = "matched" if key in matched else "reference_only"
            for target, name in ((by_token, token), (by_class, event_class)):
                slot = target.setdefault(name, bucket())
                slot["expected_events"] += 1
                if state == "matched":
                    slot["matched_events"] += 1
                else:
                    slot["reference_only_events"] += 1
            blocks.setdefault(block, {"snapshot": set(), "reference": set()})[
                "reference"
            ].add(key)
        for key, log in snapshot_index.items():
            token = contracts.contract_for_address(self.cfg.registry, log["address"]) or "unknown"
            spec = self.topics.get(log["topics"][0].lower())
            event_class = spec.event_class if spec else "unknown"
            block = int(log["blockNumber"], 16)
            for target, name in ((by_token, token), (by_class, event_class)):
                slot = target.setdefault(name, bucket())
                slot["retrieved_events"] += 1
                if key not in reference_index:
                    slot["snapshot_only_events"] += 1
            blocks.setdefault(block, {"snapshot": set(), "reference": set()})[
                "snapshot"
            ].add(key)

        parity = sum(
            1
            for sets in blocks.values()
            if sets["snapshot"] == sets["reference"]
        )
        denominator = max(len(reference_index), len(snapshot_index))
        coverage = {
            "coverage": ratio(len(matched), len(reference_index)),
            # 严格口径把快照独有的日志也算进分母：两个方向任一有缺口都不再是 1.0。
            "strict_coverage": ratio(len(matched), denominator),
            "expected_events": len(reference_index),
            "retrieved_events": len(snapshot_index),
            "matched_events": len(matched),
            "snapshot_only_events": len(snapshot_only),
            "reference_only_events": len(missing_from_snapshot),
            "block_level_log_parity": {"blocks": len(blocks), "matched": parity},
            "by_token": {name: dict(sorted(values.items())) for name, values in sorted(by_token.items())},
            "by_event_class": {
                name: dict(sorted(values.items())) for name, values in sorted(by_class.items())
            },
            "reference_retrieval": {
                "provider_labels": reference.provider_labels,
                "retrieved_at_utc": reference.retrieved_at_utc,
                "complete": reference.complete,
                "errors": reference.errors,
                "scope": (
                    "独立端点 eth_getLogs 在 [observation_start_block, study_cutoff_block] "
                    "内按代币 × 事件 topic 全量检索；分页截断的区块区间递归对半切分"
                ),
                "denominator": (
                    "独立端点在同一区块范围内返回的日志总数（按 (tx_hash, log_index) 去重），"
                    "分母含被排除项与快照缺失项"
                ),
            },
        }

        gaps: list[dict] = []
        for key in missing_from_snapshot:
            log = reference_index[key]
            spec = self.topics.get(log["topics"][0].lower())
            gaps.append(
                {
                    "tx_hash": key[0],
                    "log_index": str(key[1]),
                    "block_number": str(int(log["blockNumber"], 16)),
                    "token_symbol": contracts.contract_for_address(
                        self.cfg.registry, log["address"]
                    )
                    or "",
                    "event_class": spec.event_class if spec else "unknown",
                    "snapshot_present": "false",
                    "reference_present": "true",
                    "reason": "R1：独立端点在范围内可检索，但固定日志快照中没有该日志",
                }
            )
        for key in snapshot_only:
            log = snapshot_index[key]
            spec = self.topics.get(log["topics"][0].lower())
            gaps.append(
                {
                    "tx_hash": key[0],
                    "log_index": str(key[1]),
                    "block_number": str(int(log["blockNumber"], 16)),
                    "token_symbol": contracts.contract_for_address(
                        self.cfg.registry, log["address"]
                    )
                    or "",
                    "event_class": spec.event_class if spec else "unknown",
                    "snapshot_present": "true",
                    "reference_present": "false",
                    "reason": "快照中有、独立端点范围内未返回：事件保留在核心集，位置核对记 unverifiable",
                }
            )
        return coverage, gaps, [reference_index[key] for key in missing_from_snapshot]

    # --------------------------------------------------------- R1 placeholders
    def _reference_only_rows(
        self, logs: list[dict], tx_counts: Counter
    ) -> list[dict]:
        rows: list[dict] = []
        for log in logs:
            row = {field: "" for field in self.dictionary["events"]}
            topic0 = log["topics"][0].lower()
            spec = self.topics[topic0]
            token = contracts.contract_for_address(self.cfg.registry, log["address"])
            affected, decode_source, destruction_raw = contracts.decode_event_log(log, spec)
            block = int(log["blockNumber"], 16)
            decimals = self.cfg.tokens[token]["decimals"]
            row.update(
                {
                    "event_id": _event_id(self.cfg.chain["name"], log),
                    "chain": self.cfg.chain["name"],
                    "chain_id": str(self.cfg.chain["chain_id"]),
                    "token_symbol": token,
                    "token_contract": log["address"].lower(),
                    "block_number": str(block),
                    "block_hash": log.get("blockHash", "").lower(),
                    "event_time_precision": "second",
                    "tx_hash": log["transactionHash"].lower(),
                    "tx_index": str(int(log["transactionIndex"], 16)),
                    "log_index": str(int(log["logIndex"], 16)),
                    "log_order_key": _position(log),
                    "tx_event_count": str(
                        tx_counts.get(log["transactionHash"].lower(), 1)
                    ),
                    "event_topic0": topic0,
                    "event_signature": spec.signature,
                    "raw_topics": canonical_json(log["topics"]),
                    "raw_data": log["data"],
                    "event_class": spec.event_class,
                    "affected_address": affected,
                    "address_decode_source": decode_source,
                    "destruction_amount_raw": destruction_raw or "",
                    "destruction_amount_token": (
                        token_amount(destruction_raw, decimals) if destruction_raw else ""
                    ),
                    "token_decimals": str(decimals),
                    "balance_pre_event_state_block": str(block - 1),
                    "source_endpoint_label": self.cfg.primary.label,
                    "source_query": (
                        "reference eth_getLogs 全量检索（该日志不在固定快照中）"
                    ),
                    "source_retrieved_at_utc": "",
                    "raw_log_path": self.cfg.rel(self.cfg.log_snapshot),
                    "raw_log_sha256": self.snapshot_sha,
                    "dataset_contract_version": self.cfg.registry[
                        "dataset_contract_version"
                    ],
                    "contract_version_confidence": "low",
                    "missingness_status": "unavailable",
                    "missing_fields": ";".join(LOG_EXCLUDED_FIELDS),
                    "missing_reason_code": "log_not_retrievable",
                    "verification_status": "unverifiable",
                    "analysis_eligibility": "excluded",
                    "exclusion_rule_id": "R1",
                    "exclusion_reason": (
                        "独立端点在研究范围内可检索该日志，固定日志快照中没有；"
                        "保留占位行并计入覆盖分母"
                    ),
                }
            )
            rows.append(row)
        return rows

    # ------------------------------------------------------------ eligibility
    def _apply_eligibility(self, events: list[dict]) -> list[dict]:
        for event in events:
            if event["exclusion_rule_id"] in {"R1", "R7", "R8"}:
                event["analysis_eligibility"] = "excluded"
            elif event["verification_status"] == "unverifiable":
                event["analysis_eligibility"] = "core_unverifiable"
            else:
                event["analysis_eligibility"] = "core"
        return events

    def _exclusions(self, events: list[dict]) -> list[dict]:
        rows: list[dict] = []
        for event in events:
            if event["analysis_eligibility"] != "excluded":
                continue
            rows.append(
                {
                    "table": "events",
                    "record_key": event["event_id"],
                    "exclusion_rule_id": event["exclusion_rule_id"],
                    "analysis_eligibility": event["analysis_eligibility"],
                    "reason": event["exclusion_reason"],
                    "detail": (
                        f"{event['token_symbol']} {event['event_class']} "
                        f"block={event['block_number']} log={event['log_index']}"
                    ),
                }
            )
        for event in getattr(self, "_duplicates", []):
            rows.append(
                {
                    "table": "events",
                    "record_key": event["event_id"],
                    "exclusion_rule_id": "R7",
                    "analysis_eligibility": "excluded",
                    "reason": "重复 event_id：保留首次（按 log_order_key 最小），其余排除",
                    "detail": (
                        f"{event['token_symbol']} {event['event_class']} "
                        f"block={event['block_number']} log={event['log_index']}"
                    ),
                }
            )
        return rows

    # --------------------------------------------------------- verification
    def _version_rows(self, events: list[dict]) -> tuple[list[dict], dict]:
        """One check per distinct (token, code hash): code is constant between upgrades."""
        by_hash: dict[tuple[str, str], dict] = {}
        for event in events:
            if not event["contract_code_hash"]:
                continue
            key = (event["token_symbol"], event["contract_code_hash"])
            by_hash.setdefault(key, event)

        rows: list[dict] = []
        checked = 0
        for (token, code_hash), event in sorted(by_hash.items()):
            try:
                probe = self.rpc.probe(
                    "eth_getCode",
                    [event["implementation_address"], hex(int(event["block_number"]))],
                    provider="reference_archive",
                )
                code = probe.result
                if not isinstance(code, str) or len(code) <= 2:
                    raise RpcError(
                        f"eth_getCode 返回空代码：{str(code)[:20]}（区块 {event['block_number']}）"
                    )
                reference_hash = contracts.keccak_hex(
                    bytes.fromhex(code.removeprefix("0x"))
                )
                rows.append(
                    validation_row(
                        "order_version",
                        "contract_code_hash",
                        code_hash,
                        reference_hash,
                        f"{probe.provider_label} eth_getCode at block "
                        f"{event['block_number']}（该代码哈希的代表事件）",
                        event_id=event["event_id"],
                        reference_at=probe.retrieved_at_utc,
                        resolution=(
                            "代码哈希在实现升级之间不变：按每个不同代码哈希核对一次，"
                            f"覆盖 {sum(1 for row in events if row['contract_code_hash'] == code_hash)} 个事件"
                        ),
                    )
                )
                checked += 1
            except (RpcError, ValueError):
                rows.append(
                    validation_row(
                        "order_version",
                        "contract_code_hash",
                        code_hash,
                        "reference_unavailable",
                        "独立归档端点不可用",
                        event_id=event["event_id"],
                        deviation="reference_missing",
                        reason="external_source_unavailable",
                        resolution="R3",
                        agreement="unverifiable",
                    )
                )
        return rows, {"distinct_code_hashes": len(by_hash), "checked": checked}

    def _cutoff_population_rows(self, balances: list[dict]) -> list[dict]:
        """D2 rows for addresses that were frozen before the observation window.

        ``definitions.md`` 3.3 defines the D2 population as *every* address whose
        blacklist state is still ``blacklisted`` at the cutoff. An address frozen
        before the window and never unfrozen belongs to that population even
        though it has no event in the window, so it must not be dropped by the
        event-driven path.
        """
        delivered = {
            (row["token_symbol"], row["address"])
            for row in balances
            if row["balance_reference"] == "at_study_cutoff"
        }
        state: dict[tuple[str, str], str] = {}
        first_block: dict[tuple[str, str], int] = {}
        for log in self.snapshot["logs"]:
            spec = self.topics.get(log["topics"][0].lower())
            if spec is None:
                continue
            token = contracts.contract_for_address(self.cfg.registry, log["address"])
            if token is None:
                continue
            address, _, _ = contracts.decode_event_log(log, spec)
            key = (token, address)
            block = int(log["blockNumber"], 16)
            if spec.event_class == "freezing":
                state[key] = "blacklisted"
                first_block.setdefault(key, block)
            elif spec.event_class == "unfreezing":
                state[key] = "not_blacklisted"
            elif key not in state:
                # destruction requires a blacklisted target; seed the population
                # state but never let it flip the bit.
                state[key] = "blacklisted"
                first_block.setdefault(key, block)

        still = sorted(key for key, value in state.items() if value == "blacklisted")
        missing = [key for key in still if key not in delivered]
        if not missing:
            return []

        cutoff_header = self.rpc.probe(
            "eth_getBlockByNumber", [hex(self.cutoff_block), False]
        ).result
        rows: list[dict] = []
        for token, address in missing:
            contract = self.cfg.tokens[token]["contract"]
            blacklist_fn = self.cfg.tokens[token]["blacklist_state_function"]
            state_call = self.rpc.state_call(
                to=contract,
                data=contracts.encode_address_call(blacklist_fn, address),
                block_number=self.cutoff_block,
                label="prewindow_blacklist_state_at_cutoff",
            )
            label = blacklist_state_label(state_call.result)
            if label != "blacklisted":
                self.notes.append(
                    f"D2: {token} {address} 在观察期前冻结，但截止日状态为 {label}，不补行"
                )
                continue
            balance_call = self.rpc.state_call(
                to=contract,
                data=contracts.encode_address_call("balanceOf(address)", address),
                block_number=self.cutoff_block,
                label="prewindow_balance_at_study_cutoff",
            )
            raw = contracts.word_to_int(contracts.word_at(balance_call.result, 0))
            rows.append(
                {
                    "query_id": balance_call.query_id,
                    "chain": self.cfg.chain["name"],
                    "token_symbol": token,
                    "token_contract": contract,
                    "address": address,
                    "balance_reference": "at_study_cutoff",
                    "reference_event_id": "",
                    "balance_block_number": str(self.cutoff_block),
                    "balance_block_hash": cutoff_header["hash"].lower(),
                    "balance_raw": str(raw),
                    "balance_token": token_amount(raw, self.cfg.tokens[token]["decimals"]),
                    "token_decimals": str(self.cfg.tokens[token]["decimals"]),
                    "blacklist_state_at_balance_block": "blacklisted",
                    "balance_source": "archive_eth_call",
                    "query_payload_sha256": balance_call.payload_sha256,
                    "queried_at_utc": balance_call.queried_at_utc,
                    "missingness_status": "complete",
                    "missing_reason_code": "",
                    "notes": (
                        "D2 总体补齐：观察期前已冻结且在 study_cutoff_block 仍为 blacklisted，"
                        f"观察期内无事件；首次冻结区块 {first_block[(token, address)]}"
                    ),
                }
            )
        return rows

    def _cutoff_rows(self, balances: list[dict]) -> list[dict]:
        """D2 rows: independent re-derivation of the still-blacklisted balance."""
        cutoff_rows = [
            row for row in balances if row["balance_reference"] == "at_study_cutoff"
        ]

        def check(row: dict) -> dict:
            token = row["token_symbol"]
            address = row["address"]
            blacklist_fn = self.cfg.tokens[token]["blacklist_state_function"]
            try:
                state_probe = self.rpc.probe(
                    "eth_call",
                    [
                        {
                            "to": row["token_contract"],
                            "data": contracts.encode_address_call(blacklist_fn, address),
                        },
                        hex(self.cutoff_block),
                    ],
                    provider="reference_archive",
                )
                state = blacklist_state_label(state_probe.result)
                if state != "blacklisted":
                    return validation_row(
                        "balance_state",
                        "balance_reference",
                        row["balance_reference"],
                        f"reference state={state}",
                        "独立归档端点在 study_cutoff_block 的黑名单状态",
                        reference_at=state_probe.retrieved_at_utc,
                        deviation="address_not_blacklisted_at_cutoff",
                        reason="reference_state_disagrees",
                        resolution="R3：D2 总体归属无法独立核验",
                        agreement="unverifiable",
                    )
                balance_probe = self.rpc.probe(
                    "eth_call",
                    [
                        {
                            "to": row["token_contract"],
                            "data": contracts.encode_address_call("balanceOf(address)", address),
                        },
                        hex(self.cutoff_block),
                    ],
                    provider="reference_archive",
                )
                reference_raw = str(
                    contracts.word_to_int(contracts.word_at(balance_probe.result, 0))
                )
                self._record_balance_error(
                    "at_study_cutoff",
                    row["balance_raw"],
                    reference_raw,
                    int(row["token_decimals"]),
                )
                return validation_row(
                    "balance_state",
                    "balance_raw",
                    row["balance_raw"],
                    reference_raw,
                    f"{balance_probe.provider_label} eth_call balanceOf at "
                    f"study_cutoff_block（独立端点；D2）",
                    reference_at=balance_probe.retrieved_at_utc,
                    resolution="D2 只在仍冻结地址集上取值；与参考余额逐地址比对",
                )
            except RpcError:
                self.non_comparable_checks += 1
                return validation_row(
                    "balance_state",
                    "balance_raw",
                    row["balance_raw"],
                    "reference_unavailable",
                    "独立归档端点不可用",
                    deviation="reference_missing",
                    reason="external_source_unavailable",
                    resolution="R3",
                    agreement="unverifiable",
                )

        if not cutoff_rows:
            return []
        checked: list[tuple[tuple[str, str], dict]] = []
        with ThreadPoolExecutor(max_workers=max(1, self.workers)) as pool:
            futures = {
                pool.submit(check, row): (row["token_symbol"], row["address"])
                for row in cutoff_rows
            }
            for future in as_completed(futures):
                checked.append((futures[future], future.result()))
        # 线程完成顺序不稳定；按（代币，地址）排序，保证复跑得到同一行序。
        checked.sort(key=lambda item: item[0])
        return [row for _, row in checked]

    def _table_rows(
        self,
        bundle: dict,
        coverage: dict,
        decision: dict,
        decision_sha: str,
        reference: ReferenceLogSet,
    ) -> list[dict]:
        rows: list[dict] = []
        events = bundle["events"]
        matches = [row for row in bundle["validation"] if row["check_type"] == "manual_reference"]
        unmatched = [
            row for row in matches if row["contract_value"] == "unmatched"
        ]
        rows.append(
            validation_row(
                "manual_reference",
                "affected_address",
                f"unmatched_events={len(unmatched)}; excluded_by_match=0",
                "匹配层不参与核心集剔除",
                "OFAC SDN 快照字符串比对（结果只作为核对行，不作为过滤条件）",
                resolution=(
                    "核心集覆盖全部可检索事件：匹配失败/缺失只影响 matches 层（工单 06），"
                    "不影响 analysis_eligibility"
                ),
                agreement="match",
            )
        )
        expected = coverage["expected_events"]
        matched = coverage["matched_events"]
        complete = matched == expected == coverage["retrieved_events"]
        rows.append(
            validation_row(
                "source_position",
                "log_order_key",
                (
                    f"matched={matched}; expected={expected}; "
                    f"retrieved={coverage['retrieved_events']}; "
                    f"strict={coverage['strict_coverage']}"
                ),
                "independent set fully covered" if complete else "coverage gap recorded",
                "独立端点全量 eth_getLogs 检索（分母外部于快照）",
                reference_at=reference.retrieved_at_utc,
                deviation="" if complete else "coverage_gap",
                reason="" if complete else "retrieval_coverage_incomplete",
                resolution=(
                    "event retrieval coverage 的分母是独立端点在研究范围内返回的日志总数；"
                    "快照缺失项按 R1 保留占位行"
                ),
                agreement="match" if complete else "mismatch",
            )
        )
        rows.append(
            validation_row(
                "manual_reference",
                "chain",
                ",".join(sorted({event["chain"] for event in events})),
                self.cfg.chain["name"],
                f"config/study_scope.json v{decision.get('decision_version', '?')} "
                f"（sha256 {decision_sha[:8]}）",
                resolution="每条事件行登记所属链；其他链只在 chain_registry 登记，不采集",
                agreement="match",
            )
        )
        rows.append(
            validation_row(
                "schema_conformance",
                "event_id",
                (
                    f"{len(events)} events / {len(bundle['balances'])} balances / "
                    f"{len(bundle['validation'])} validation / "
                    f"{len(bundle['chain_registry'])} chain rows"
                ),
                "docs/data-dictionary.csv field list",
                "docs/data-dictionary.csv",
                resolution="四张交付表列集合与字典一致，写出前断言",
                agreement="match",
            )
        )
        return rows

    # ------------------------------------------------------------------ runner
    def run(self) -> dict:
        decision, decision_sha = self._load_decision()
        if self.start_block != int(decision.get("observation_start_block", 0)):
            raise SystemExit(
                f"--start-block {self.start_block} 与决策 "
                f"{decision.get('observation_start_block')} 不一致"
            )
        if self.cutoff_block != int(decision.get("study_cutoff_block", 0)):
            raise SystemExit(
                f"--cutoff-block {self.cutoff_block} 与决策 "
                f"{decision.get('study_cutoff_block')} 不一致"
            )

        self._decision = decision
        inside, outside = self._logs()
        if self.max_events is not None:
            inside = inside[: self.max_events]
            self.notes.append(
                f"--max-events={self.max_events}：冒烟运行，覆盖与指标只对截断后的事件集成立，"
                "不作为交付口径"
            )
        print(
            f"core range       : blocks {self.start_block:,}–{self.cutoff_block:,}; "
            f"{len(inside)} events in range, {len(outside)} outside",
            file=sys.stderr,
            flush=True,
        )

        print("retrieving independent log set ...", file=sys.stderr, flush=True)
        reference = RangeLogRetriever(self.cfg).fetch(
            self.start_block, self.cutoff_block
        )
        coverage, gaps, reference_only_logs = self._coverage(inside, reference)
        reference_positions = reference.positions()
        print(
            f"coverage         : {coverage['matched_events']}/{coverage['expected_events']} "
            f"matched, snapshot_only={coverage['snapshot_only_events']}, "
            f"reference_only={coverage['reference_only_events']}",
            file=sys.stderr,
            flush=True,
        )

        print("reconstructing core events ...", file=sys.stderr, flush=True)
        outcomes = self._run_events(
            inside, reference_positions, reference.retrieved_at_utc
        )
        # R7 在合并前判定：merge_bundles 会按 event_id 去重，重复项若不留记录就会消失。
        raw_events = sorted(
            (event for outcome in outcomes for event in outcome["events"]),
            key=lambda event: event["log_order_key"],
        )
        seen_ids: set[str] = set()
        self._duplicates = []
        for event in raw_events:
            if event["event_id"] in seen_ids:
                self._duplicates.append(event)
            else:
                seen_ids.add(event["event_id"])
        bundle = merge_bundles(outcomes)
        events = bundle["events"]
        reference_tx_counts = Counter(
            log["transactionHash"].lower() for log in reference.logs
        )
        events.extend(
            self._reference_only_rows(reference_only_logs, reference_tx_counts)
        )
        events.sort(key=lambda event: event["log_order_key"])
        bundle["events"] = events
        self._apply_eligibility(events)

        print("verifying contract versions ...", file=sys.stderr, flush=True)
        version_rows, version_stats = self._version_rows(events)

        print("verifying D2 balances ...", file=sys.stderr, flush=True)
        prewindow_rows = self._cutoff_population_rows(bundle["balances"])
        if prewindow_rows:
            bundle["balances"].extend(prewindow_rows)
            self.prewindow_cutoff_added = len(prewindow_rows)
            print(
                f"D2 population    : added {len(prewindow_rows)} pre-window addresses "
                "still blacklisted at cutoff",
                file=sys.stderr,
                flush=True,
            )
        cutoff_rows = self._cutoff_rows(bundle["balances"])
        bundle["validation"].extend(version_rows)
        bundle["validation"].extend(cutoff_rows)
        bundle["chain_registry"] = self._chain_rows(decision)

        bundle["validation"].extend(
            self._table_rows(bundle, coverage, decision, decision_sha, reference)
        )

        for index, row in enumerate(bundle["validation"], start=1):
            row["validation_id"] = f"vd_{index:05d}"
        evidence = [
            row["reference_retrieved_at_utc"]
            for row in bundle["validation"]
            if row["reference_retrieved_at_utc"]
            and row["check_type"] != "schema_conformance"
        ]
        reviewed_at = max(evidence) if evidence else now_utc()
        for row in bundle["validation"]:
            if not row["reference_retrieved_at_utc"]:
                row["reference_retrieved_at_utc"] = reviewed_at
            row["reviewed_at_utc"] = reviewed_at

        metrics = self._metrics(bundle, coverage, len(outside))
        return {
            "bundle": bundle,
            "coverage": coverage,
            "gaps": gaps,
            "partial": self.max_events is not None,
            "scope_excluded": [
                self._scope_row(log, decision) for log in outside
            ],
            "exclusions": self._exclusions(events),
            "metrics": metrics,
            "decision": decision,
            "decision_sha256": decision_sha,
            "version_stats": version_stats,
        }

    def _scope_row(self, log: dict, decision: dict) -> dict:
        topic0 = log["topics"][0].lower()
        spec = self.topics.get(topic0)
        token = contracts.contract_for_address(self.cfg.registry, log["address"]) or ""
        block = int(log["blockNumber"], 16)
        reason = (
            "before_observation_start"
            if block < self.start_block
            else "after_study_cutoff"
        )
        return {
            "event_id": _event_id(self.cfg.chain["name"], log),
            "chain": self.cfg.chain["name"],
            "token_symbol": token,
            "event_class": spec.event_class if spec else "unknown",
            "block_number": str(block),
            "tx_hash": log["transactionHash"].lower(),
            "log_index": str(int(log["logIndex"], 16)),
            "log_order_key": _position(log),
            "reason": reason,
            "observation_start_block": str(self.start_block),
            "study_cutoff_block": str(self.cutoff_block),
            "basis": (
                "事件在观察期外，不进入核心集，也不进入任何 issuer 比较；"
                "逐条登记以便复核排除边界（docs/study-scope.md §1）"
            ),
            "decision_version": str(decision.get("decision_version", "")),
        }

    def _chain_rows(self, decision: dict) -> list[dict]:
        return [
            {
                "chain": self.cfg.chain["name"],
                "chain_id": str(self.cfg.chain["chain_id"]),
                "in_scope": "verified_sample",
                "scope_basis": (
                    "config/study_scope.json "
                    + str(decision.get("decision_version", ""))
                    + "：反馈把小型代表性样本限定在 Ethereum，本工单核心集同链"
                ),
                "rpc_endpoint_label": self.cfg.primary.label,
                "notes": "核心集全部事件所在链；链字段逐事件登记",
            },
            {
                "chain": "tron",
                "chain_id": "",
                "in_scope": "registered_pending",
                "scope_basis": (
                    "spec Further Notes 待确认项：核心分析的链范围未由反馈界定，"
                    "本工单不预设扩展"
                ),
                "rpc_endpoint_label": "",
                "notes": "仅登记、不采集；扩展须先补验证并重做研究期决策",
            },
        ]

    # ---------------------------------------------------------------- metrics
    def _record_balance_error(
        self, bucket: str, contract_value: str, reference_value: str, decimals: int
    ) -> bool:
        """Accumulate one balance comparison; non-numeric rows are counted apart."""
        if not _is_int(contract_value) or not _is_int(reference_value):
            self.non_comparable_checks += 1
            return False
        error = abs(int(contract_value) - int(reference_value))
        stats = self.balance_errors.setdefault(
            bucket,
            {
                "checks": 0,
                "exact": 0,
                "sum_abs_raw": 0,
                "max_abs_raw": 0,
                "max_abs_token": Decimal(0),
            },
        )
        stats["checks"] += 1
        stats["exact"] += int(error == 0)
        stats["sum_abs_raw"] += error
        stats["max_abs_raw"] = max(stats["max_abs_raw"], error)
        stats["max_abs_token"] = max(
            stats["max_abs_token"], Decimal(error) / (Decimal(10) ** decimals)
        )
        return True

    def _metrics(self, bundle: dict, coverage: dict, outside_count: int) -> dict:
        events = bundle["events"]
        balances = bundle["balances"]
        validation = bundle["validation"]

        by_event = {event["event_id"]: event for event in events}
        seen: set[str] = set()
        for row in validation:
            if (
                row["check_type"] != "balance_state"
                or row["field_name"] != "balance_pre_event_raw"
                or not row["event_id"]
                or row["event_id"] in seen
            ):
                continue
            seen.add(row["event_id"])
            event = by_event.get(row["event_id"])
            if event is None:
                continue
            if event["event_class"] == "freezing":
                if event["blacklist_state_before"] != "not_blacklisted":
                    continue
                bucket = "at_freeze"
            elif event["event_class"] in {"unfreezing", "destruction"}:
                bucket = "at_event"
            else:
                continue
            self._record_balance_error(
                bucket,
                row["contract_value"],
                row["reference_value"],
                int(event["token_decimals"]),
            )

        total_checks = total_exact = total_sum = total_max = 0
        max_token = Decimal(0)
        by_reference: dict[str, dict] = {}
        for bucket, stats in sorted(self.balance_errors.items()):
            output = dict(stats)
            output["mean_abs_error_raw"] = (
                format(Decimal(output["sum_abs_raw"]) / output["checks"], ".6f")
                if output["checks"]
                else "0.000000"
            )
            output["max_abs_error_token"] = format(output["max_abs_token"], ".6f")
            output.pop("max_abs_token")
            by_reference[bucket] = output
            total_checks += output["checks"]
            total_exact += output["exact"]
            total_sum += output["sum_abs_raw"]
            total_max = max(total_max, output["max_abs_raw"])
            max_token = max(max_token, Decimal(output["max_abs_error_token"]))

        by_check: dict[str, dict] = {}
        for row in validation:
            bucket = by_check.setdefault(
                row["check_type"], {"rows": 0, "unverifiable": 0, "mismatch": 0}
            )
            bucket["rows"] += 1
            bucket["unverifiable"] += int(row["agreement"] == "unverifiable")
            bucket["mismatch"] += int(row["agreement"] == "mismatch")
        for bucket in by_check.values():
            bucket["share"] = ratio(bucket["unverifiable"], bucket["rows"])
        unverifiable = sum(1 for row in validation if row["agreement"] == "unverifiable")

        eligibility = Counter(event["analysis_eligibility"] for event in events)
        rules = Counter(
            event["exclusion_rule_id"]
            for event in events
            if event["exclusion_rule_id"]
        )
        episode_starts = sum(
            1
            for event in events
            if event["event_class"] == "freezing"
            and event["blacklist_state_before"] == "not_blacklisted"
        )
        still_frozen = sum(
            1 for row in balances if row["balance_reference"] == "at_study_cutoff"
        )
        versions = sorted(
            {
                event["contract_version_label"]
                for event in events
                if event["contract_version_label"]
            }
        )
        low_confidence = sum(
            1 for event in events if event["contract_version_confidence"] == "low"
        )
        unverifiable_events = sum(
            1 for event in events if event["verification_status"] == "unverifiable"
        )
        r9_rows = sum(
            1
            for event in events
            if event["event_class"] in {"freezing", "unfreezing"}
            and event["blacklist_transition"] == "false"
        )
        record_total = len(events) + len(balances)

        return {
            "scope": (
                "研究范围内全量核心集（观察期由 config/study_scope.json 固定）；"
                "受限子集指标不在本工单"
            ),
            "event_retrieval_coverage": coverage,
            "balance_reconstruction_error": {
                "checks": total_checks,
                "exact_matches": total_exact,
                "max_abs_error_raw": str(total_max),
                "mean_abs_error_raw": (
                    format(Decimal(total_sum) / total_checks, ".6f")
                    if total_checks
                    else "0.000000"
                ),
                "max_abs_error_token": format(max_token, ".6f"),
                "not_comparable": self.non_comparable_checks,
                "by_reference": by_reference,
            },
            "unverifiable_share": {
                "validation_rows": len(validation),
                "unverifiable": unverifiable,
                "share": ratio(unverifiable, len(validation)),
                "records": record_total,
                "unverifiable_records": unverifiable_events,
                "record_share": ratio(unverifiable_events, record_total),
                "by_check_type": by_check,
                "note": (
                    "行口径与工单 04 一致；records 口径按 definitions.md §7 的"
                    "\"verification_status = unverifiable 的记录占比\"另报，"
                    "两者分母不同，不得混用"
                ),
            },
            "exclusions": {
                "by_rule_id": dict(sorted(rules.items())),
                "outside_observation_window": outside_count,
                "match_based_exclusions": 0,
                "note": (
                    "R1/R7/R8 才排除记录；R2/R3/R4/R9 保留在核心集并标注原因。"
                    "匹配结果不参与任何剔除"
                ),
            },
            "d2_population": {
                "addresses_still_blacklisted_at_cutoff": still_frozen,
                "prewindow_only_added": self.prewindow_cutoff_added,
                "definition": (
                    "definitions.md 3.3：cutoff 结束时仍为 blacklisted 的全部地址；"
                    "含观察期前已冻结且未解冻、观察期内无事件的地址"
                ),
            },
            "core_set": {
                "events": len(events),
                "event_class": dict(
                    sorted(Counter(event["event_class"] for event in events).items())
                ),
                "by_token": dict(
                    sorted(Counter(event["token_symbol"] for event in events).items())
                ),
                "balances": len(balances),
                "balance_reference": dict(
                    sorted(Counter(row["balance_reference"] for row in balances).items())
                ),
                "episode_starts": episode_starts,
                "addresses_still_blacklisted_at_cutoff": still_frozen,
                "analysis_eligibility": dict(sorted(eligibility.items())),
                "contract_versions": versions,
                "low_confidence_versions": low_confidence,
                "r9_attribution_rows": r9_rows,
                "chains": sorted({event["chain"] for event in events}),
            },
        }

    # ------------------------------------------------------------------ check
    def check(self) -> list[str]:
        """Offline re-check of a delivered core bundle (no network).

        Recomputes the data digest from the written CSVs, re-asserts the column
        contract, and checks the invariants that make the core set auditable:
        every event carries a registered in-scope chain, no event is excluded by
        matching, excluded rows all have a rule id, and coverage denominators are
        non-zero and internally consistent.
        """
        _, decision_sha = self._load_decision()
        core_id = self.core_id(decision_sha)
        out_dir = self.cfg.out_root / core_id
        manifest_path = out_dir / "manifest.json"
        if not manifest_path.exists():
            return [f"核心集产物缺失：{self.cfg.rel(manifest_path)}"]

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        problems: list[str] = []
        delivered: dict[str, list[dict]] = {}
        for table in TABLE_ORDER:
            path = out_dir / f"{table}.csv"
            if not path.exists():
                problems.append(f"{table}.csv 缺失")
                continue
            with path.open(encoding="utf-8", newline="") as handle:
                rows = [dict(row) for row in csv.DictReader(handle)]
            delivered[table] = rows
            try:
                assert_columns(table, self.dictionary[table], rows)
            except ValueError as exc:
                problems.append(str(exc))
        if len(delivered) != len(TABLE_ORDER):
            return problems

        recomputed = data_digest(delivered)
        if recomputed != manifest["digests"]["data_digest"]:
            problems.append(
                "交付 CSV 重算的数据摘要与 manifest 不一致："
                f"{recomputed} != {manifest['digests']['data_digest']}"
            )

        registry = {row["chain"]: row for row in delivered["chain_registry"]}
        for event in delivered["events"]:
            chain = registry.get(event["chain"])
            if chain is None:
                problems.append(f"{event['event_id']} 的链 {event['chain']} 未登记")
            elif chain["in_scope"] != "verified_sample":
                problems.append(
                    f"{event['event_id']} 的链 {event['chain']} 不在采集范围"
                )

        for event in delivered["events"]:
            if (
                event["exclusion_rule_id"] and event["analysis_eligibility"] != "excluded"
            ):
                problems.append(
                    f"{event['event_id']} 有排除规则但资格不是 excluded"
                )
            if event["analysis_eligibility"] == "excluded" and not event["exclusion_rule_id"]:
                problems.append(f"{event['event_id']} 被排除但没有规则 id")

        coverage = manifest["metrics"]["event_retrieval_coverage"]
        if coverage["expected_events"] == 0:
            problems.append("覆盖率分母为 0")
        if coverage["matched_events"] > coverage["expected_events"]:
            problems.append("覆盖率分子大于分母")
        if coverage["snapshot_only_events"] + coverage["matched_events"] != coverage[
            "retrieved_events"
        ]:
            problems.append("快照侧覆盖算术不一致")
        if coverage["reference_only_events"] + coverage["matched_events"] != coverage[
            "expected_events"
        ]:
            problems.append("参照侧覆盖算术不一致")
        if coverage.get("strict_coverage") != "1.000000":
            problems.append(
                f"严格覆盖率不是 1.0：{coverage.get('strict_coverage')}（存在快照/参照差集）"
            )

        # 从原始日志重算"观察期内应有的事件集合"，与交付事件表对照（R1 占位行除外）。
        if not manifest.get("partial_run"):
            inside, _ = self._logs()
            snapshot_keys = {_position(log) for log in inside}
            r1_rows = [
                event
                for event in delivered["events"]
                if event["exclusion_rule_id"] == "R1"
            ]
            delivered_keys = {
                event["log_order_key"]
                for event in delivered["events"]
                if event["exclusion_rule_id"] != "R1"
            }
            if snapshot_keys != delivered_keys:
                problems.append(
                    "交付事件集与固定快照在观察期内的日志集合不一致："
                    f"缺少 {len(snapshot_keys - delivered_keys)} 条、"
                    f"多出 {len(delivered_keys - snapshot_keys)} 条"
                )
            if len(delivered_keys) + len(r1_rows) != len(delivered["events"]):
                problems.append("事件表存在重复 log_order_key")

        exclusions = manifest["metrics"]["exclusions"]
        if exclusions.get("match_based_exclusions") != 0:
            problems.append("存在按匹配结果剔除的事件")
        outside = manifest["counts"]["outside_observation_window"]
        declared_outside = exclusions.get("outside_observation_window", -1)
        if outside != declared_outside:
            problems.append(
                f"观察期外事件计数不一致：{outside} != {declared_outside}"
            )

        scope_path = out_dir / "scope_excluded.csv"
        if scope_path.exists():
            with scope_path.open(encoding="utf-8", newline="") as handle:
                excluded_rows = [dict(row) for row in csv.DictReader(handle)]
            if len(excluded_rows) != outside:
                problems.append(
                    "scope_excluded.csv 行数与 manifest 的观察期外计数不一致"
                )

        return problems

    # --------------------------------------------------------------- delivery
    def core_id(self, decision_sha: str) -> str:
        return core_id_for(
            self.cfg,
            self.snapshot_sha,
            decision_sha,
            self.start_block,
            self.cutoff_block,
        )

    def deliver(self, outcome: dict) -> dict:
        bundle = outcome["bundle"]
        for table, rows in bundle.items():
            assert_columns(table, self.dictionary[table], rows)

        core_id = self.core_id(outcome["decision_sha256"])
        out_dir = self.cfg.out_root / core_id
        written, parquet_written = write_bundle(out_dir, self.dictionary, bundle)

        exclusions_path = self.cfg.path(EXCLUSIONS_PATH)
        write_csv(
            exclusions_path,
            [
                "table",
                "record_key",
                "exclusion_rule_id",
                "analysis_eligibility",
                "reason",
                "detail",
            ],
            outcome["exclusions"],
        )
        scope_path = out_dir / "scope_excluded.csv"
        write_csv(
            scope_path,
            [
                "event_id",
                "chain",
                "token_symbol",
                "event_class",
                "block_number",
                "tx_hash",
                "log_index",
                "log_order_key",
                "reason",
                "observation_start_block",
                "study_cutoff_block",
                "basis",
                "decision_version",
            ],
            outcome["scope_excluded"],
        )
        gaps_path = out_dir / "coverage_gaps.csv"
        write_csv(
            gaps_path,
            [
                "tx_hash",
                "log_index",
                "block_number",
                "token_symbol",
                "event_class",
                "snapshot_present",
                "reference_present",
                "reason",
            ],
            outcome["gaps"],
        )

        manifest = {
            "core_id": core_id,
            "dataset_contract_version": self.cfg.registry["dataset_contract_version"],
            "generated_at_utc": now_utc(),
            "inputs": {
                "log_snapshot": self.cfg.rel(self.cfg.log_snapshot),
                "log_snapshot_sha256": self.snapshot_sha,
                "scope_decision": self.decision_path,
                "scope_decision_sha256": outcome["decision_sha256"],
                "contract_registry": self.cfg.rel(self.cfg.contract_registry),
                "dictionary": self.cfg.rel(self.cfg.dictionary_csv),
            },
            "scope": {
                "chains": outcome["decision"].get("scope", {}).get("chains", []),
                "tokens": outcome["decision"].get("scope", {}).get("tokens", []),
                "event_classes": outcome["decision"].get("scope", {}).get(
                    "event_classes", []
                ),
                "observation_start_block": self.start_block,
                "study_cutoff_block": self.cutoff_block,
                "decision_version": outcome["decision"].get("decision_version", ""),
            },
            "endpoints": {
                "primary_label": self.cfg.primary.label,
                "reference_labels": [
                    provider.label for provider in self.cfg.reference_providers
                ],
            },
            "counts": {
                "events": len(bundle["events"]),
                "balances": len(bundle["balances"]),
                "validation": len(bundle["validation"]),
                "chain_registry": len(bundle["chain_registry"]),
                "event_class": dict(
                    sorted(
                        Counter(row["event_class"] for row in bundle["events"]).items()
                    )
                ),
                "balance_reference": dict(
                    sorted(
                        Counter(
                            row["balance_reference"] for row in bundle["balances"]
                        ).items()
                    )
                ),
                "validation_agreement": dict(
                    sorted(
                        Counter(row["agreement"] for row in bundle["validation"]).items()
                    )
                ),
                "excluded": len(outcome["exclusions"]),
                "outside_observation_window": len(outcome["scope_excluded"]),
                "r7_duplicates": len(getattr(self, "_duplicates", [])),
            },
            "metrics": outcome["metrics"],
            "partial_run": outcome.get("partial", False),
            "checks": {
                "contract_versions": outcome["version_stats"],
                "operator": (
                    "every delivered row is asserted against docs/data-dictionary.csv "
                    "before it is written"
                ),
            },
            "verification_scope": {
                "independent_per_event": [
                    "source_position",
                    "balance_pre_event_raw",
                    "balance_raw (D2)",
                    "affected_address (OFAC string match)",
                ],
                "independent_per_evidence": [
                    "contract_code_hash (once per distinct hash)",
                ],
                "not_independently_verified_per_event": [
                    "blacklist_state_before",
                    "blacklist_state_after",
                ],
                "basis": (
                    "事件状态机字段仍由归档端点取得；其独立核对依赖 D2 归属判定的"
                    "参考状态与工单 04 的人工样本，不逐事件重复取第二来源。"
                    "unverifiable share = 0 只表示本表列出的核对行都有第二来源。"
                ),
            },
            "columns_verified": True,
            "parquet_written": parquet_written,
            "cache": {
                "refresh": self.cfg.refresh,
                "state_calls_from_cache": self.cache_stats["state_calls_from_cache"],
                "probes_from_cache": self.cache_stats["probes_from_cache"],
            },
            "digests": {
                "data_digest": data_digest(bundle),
                "artifact_digest": artifact_digest(written),
                "data_digest_excludes": {
                    "events": ["source_retrieved_at_utc"],
                    "balances": ["queried_at_utc"],
                    "validation": [
                        "reference_retrieved_at_utc",
                        "reviewed_at_utc",
                    ],
                },
            },
            "workforce": {"workers": self.workers},
            "notes": self.notes
            + [
                f"×{count} {text}"
                for text, count in sorted(self.note_counts.items())
            ],
            "deviations": self.deviations,
            "reproduce_command": (
                "python scripts/reconstruct_core.py "
                f"--start-block {self.start_block} --cutoff-block {self.cutoff_block} "
                f"--workers {self.workers}"
            ),
            "outputs": {
                table: self.cfg.rel(out_dir / f"{table}.csv") for table in TABLE_ORDER
            }
            | {
                "exclusions": self.cfg.rel(exclusions_path),
                "scope_excluded": self.cfg.rel(scope_path),
                "coverage_gaps": self.cfg.rel(gaps_path),
            },
        }
        manifest_path = out_dir / "manifest.json"
        if manifest_path.exists():
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
            if (
                previous.get("digests", {}).get("data_digest")
                == manifest["digests"]["data_digest"]
                and previous.get("generated_at_utc")
            ):
                # 同一输入的复跑不改写取证时刻：与工单 04 一样，复现不应破坏时间证据。
                manifest["generated_at_utc"] = previous["generated_at_utc"]
        manifest["last_verified_at_utc"] = now_utc()
        manifest_path.write_text(
            json.dumps(manifest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        manifest["manifest_path"] = self.cfg.rel(manifest_path)
        return manifest
