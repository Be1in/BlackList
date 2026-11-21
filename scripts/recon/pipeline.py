"""Single-event reconstruction: raw log snapshot + archival state -> tables.

One pipeline, one verification boundary: the outputs are the event record, the
two balance rows and the validation rows. Parsing steps are not separately
verified (``spec.md`` → Testing Decisions).
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from . import contracts
from .config import Config
from .rpc import (
    Probe,
    RpcCache,
    RpcError,
    canonical_json,
    now_utc,
    sha256_hex,
)
from .tables import (
    PROVENANCE_TIME_FIELDS,
    assert_columns,
    artifact_digest,
    data_digest,
    load_dictionary,
    write_bundle,
)

REVIEWER = "agent:auto-cross-check"

# The core run rebuilds thousands of events from the same two input files. Parsing
# them per event would dominate the run, so the documents are cached per process
# and re-read only when the file on disk changes.
_SNAPSHOT_CACHE: dict[str, tuple[int, str, dict]] = {}
_SNAPSHOT_LOCK = threading.Lock()
_OFAC_CACHE: dict[str, tuple[tuple[int, int], dict[str, dict], dict]] = {}
_OFAC_LOCK = threading.Lock()


def load_snapshot_document(path: Path) -> tuple[dict, str]:
    """Parsed log snapshot + its sha256, shared by every reconstruction in a run."""
    key = str(path.resolve())
    stamp = path.stat().st_mtime_ns
    with _SNAPSHOT_LOCK:
        cached = _SNAPSHOT_CACHE.get(key)
    if cached is not None and cached[0] == stamp:
        return cached[2], cached[1]

    raw = path.read_bytes()
    digest = sha256_hex(raw)
    document = json.loads(raw.decode("utf-8"))
    with _SNAPSHOT_LOCK:
        _SNAPSHOT_CACHE[key] = (stamp, digest, document)
    return document, digest


def build_block_events(
    snapshot: dict, registry: dict, topics: dict
) -> dict[int, list[tuple[int, str, str, str]]]:
    """Index every registered blacklist log by block, for position-level state.

    ``blacklist_state_before/after`` must describe the event's own position, not
    the block boundaries: a destruction that follows a freezing in the same block
    does not change the blacklist bit, so its before and after are both
    ``blacklisted`` even though the block started with ``not_blacklisted``.
    """
    index: dict[int, list[tuple[int, str, str, str]]] = {}
    for log in snapshot["logs"]:
        spec = topics.get(log["topics"][0].lower())
        if spec is None:
            continue
        token = contracts.contract_for_address(registry, log["address"])
        if token is None:
            continue
        address, _, _ = contracts.decode_event_log(log, spec)
        index.setdefault(int(log["blockNumber"], 16), []).append(
            (int(log["logIndex"], 16), token, address, spec.event_class)
        )
    for entries in index.values():
        entries.sort()
    return index


def load_ofac_index(cfg: Config) -> tuple[dict[str, dict], dict]:
    """OFAC SDN address index + manifest, shared by every reconstruction in a run."""
    entries_path = cfg.ofac_dir / "sdn_digital_currency_addresses.json"
    manifest_path = cfg.ofac_dir / "sdn_manifest.json"
    key = str(entries_path.resolve())
    stamps = (entries_path.stat().st_mtime_ns, manifest_path.stat().st_mtime_ns)
    with _OFAC_LOCK:
        cached = _OFAC_CACHE.get(key)
    if cached is not None and cached[0] == stamps:
        return cached[1], cached[2]

    entries = json.loads(entries_path.read_text(encoding="utf-8"))
    index = {entry["address"].lower(): entry for entry in entries}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    with _OFAC_LOCK:
        _OFAC_CACHE[key] = (stamps, index, manifest)
    return index, manifest


def rfc3339(timestamp_seconds: int) -> str:
    moment = datetime.fromtimestamp(timestamp_seconds, tz=timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def token_amount(raw: str, decimals: int) -> str:
    quantum = Decimal(1).scaleb(-decimals)
    return format((Decimal(raw).scaleb(-decimals)).quantize(quantum), "f")


def blacklist_state_label(call_result: str) -> str:
    """Decode ``isBlackListed`` / ``isBlacklisted`` return data into the enum."""
    return (
        "blacklisted"
        if contracts.word_to_bool(contracts.word_at(call_result, 0))
        else "not_blacklisted"
    )


@dataclass
class Reconstruction:
    cfg: Config
    tx_hash: str
    cutoff_block: int
    log_index: int | None = None
    expected_token: str = ""
    # Core-set mode: positions come from one independent full-range log retrieval
    # instead of a per-event receipt, version checks are aggregated, and the
    # per-event schema row is replaced by a table-level one.
    reference_positions: dict[tuple[str, int], str] | None = None
    reference_retrieved_at: str = ""
    reference_state_provider: str = "reference"
    verify_contract_version: bool = True
    emit_schema_rows: bool = True
    ofac_index: dict[str, dict] | None = None
    # Shared per-block blacklist index for core runs: block -> [(log_index,
    # token, address, event_class)]. When omitted, a single-event reconstruction
    # builds it from the snapshot it already loaded.
    block_events: dict[int, list[tuple[int, str, str, str]]] | None = None
    notes: list[str] = field(default_factory=list)
    deviations: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.rpc = RpcCache(self.cfg)
        self.topics = contracts.verify_topic_table(self.cfg.registry)
        self.dictionary = load_dictionary(self.cfg.dictionary_csv)
        self.cache_stats = {"state_calls_from_cache": 0, "probes_from_cache": 0}
        # ``balances`` 以 query_id 为主键，同一笔交易内多个事件可能共用一次
        # eth_call（同一地址同一声明口径）或同一截止日查询，重复写入会破坏主键。
        self._emitted_query_ids: dict[str, str] = {}
        self._cutoff_done: set[tuple[str, str]] = set()

    # ------------------------------------------------------------------ helpers
    def _state_call(self, signature: str, address: str, block_number: int, label: str):
        call = self.rpc.state_call(
            to=self.token_contract,
            data=contracts.encode_address_call(signature, address),
            block_number=block_number,
            label=label,
        )
        self.cache_stats["state_calls_from_cache"] += int(call.from_cache)
        return call

    def _probe(self, method: str, params: list, provider: str = "primary") -> Probe:
        probe = self.rpc.probe(method, params, provider=provider)
        self.cache_stats["probes_from_cache"] += int(probe.from_cache)
        return probe

    def _position_state(
        self, affected: str, base_before: str, event_class: str
    ) -> tuple[str, str]:
        """Blacklist state immediately before/after this log.

        ``base_before`` is the archive state at ``block_number - 1``. Earlier
        blacklist logs in the same block are replayed in log order, the same way
        D1 replays earlier transfers; the current log then determines ``after``.
        """
        before = base_before
        for log_index, token, address, cls in self.block_events.get(
            self.block_number, ()
        ):
            if log_index >= self.log_index:
                break
            if token != self.token_symbol or address != affected:
                continue
            if cls == "freezing":
                before = "blacklisted"
            elif cls == "unfreezing":
                before = "not_blacklisted"
        after = before
        if event_class == "freezing":
            after = "blacklisted"
        elif event_class == "unfreezing":
            after = "not_blacklisted"
        return before, after

    # ---------------------------------------------------------------- step 1-2
    def _load_snapshot(self) -> tuple[dict, str, list[dict]]:
        """该交易内的全部已登记事件日志，按 ``log_index`` 升序。

        ``log_index`` 为 None 时返回交易作用域下的全部事件（同一交易多个事件各自成行）；
        指定值时只保留那一条（工单 02 的单笔模式）。
        """
        snapshot, snapshot_sha = load_snapshot_document(self.cfg.log_snapshot)

        matches = [
            log
            for log in snapshot["logs"]
            if log["transactionHash"].lower() == self.tx_hash.lower()
        ]
        if self.log_index is not None:
            matches = [
                log for log in matches if int(log["logIndex"], 16) == self.log_index
            ]
            if not matches:
                raise SystemExit(
                    f"在日志快照中找不到 tx={self.tx_hash} log_index={self.log_index}"
                )
        if not matches:
            raise SystemExit(f"日志快照中没有 tx={self.tx_hash} 的已登记事件日志")
        positions = [int(log["logIndex"], 16) for log in matches]
        if len(positions) != len(set(positions)):
            raise SystemExit(f"日志快照中位置重复: {len(positions) - len(set(positions))} 条")
        return snapshot, snapshot_sha, sorted(matches, key=lambda log: int(log["logIndex"], 16))

    def _source_query(self, snapshot: dict) -> str:
        tokens = [
            contracts.contract_for_address(self.cfg.registry, address) or address
            for address in snapshot["addresses"]
        ]
        signatures = [self.topics[topic.lower()].signature for topic in snapshot["topics"]]
        return (
            f"eth_getLogs(address=[{','.join(tokens)}]"
            f", fromBlock={snapshot['start_block']}, toBlock={snapshot['end_block']}"
            f", topics=[{','.join(signatures)}])"
        )

    # ------------------------------------------------------------------ step 3
    def _prior_transfer_adjustment(
        self, block_number: int, affected: str, provider: str, event_log_index: int
    ) -> tuple[int, int]:
        """Sum of signed Transfer amounts to/from ``affected`` earlier in the block."""
        transfer_topic = self.cfg.registry["balance_evidence_event"]["topic0"]
        params = [
            {
                "address": self.token_contract,
                "fromBlock": hex(block_number),
                "toBlock": hex(block_number),
                "topics": [transfer_topic],
            }
        ]
        probe = self._probe("eth_getLogs", params, provider=provider)
        if not isinstance(probe.result, list):
            raise RpcError(
                f"区块 {block_number} 的 Transfer 日志不是列表：{str(probe.result)[:60]}"
            )
        logs = probe.result
        adjustment = 0
        for log in logs:
            if len(log["topics"]) < 3:
                raise RpcError(f"Transfer 日志缺少 indexed 参数: {log.get('transactionHash')}")
            if int(log.get("blockNumber", "0x0"), 16) != block_number:
                raise RpcError(f"端点返回了区块范围外的日志: {log.get('blockNumber')}")
            try:
                log_index = int(log["logIndex"], 16)
            except (KeyError, TypeError, ValueError) as exc:
                raise RpcError(
                    f"日志缺少可用 logIndex: {log.get('transactionHash')}"
                ) from exc
            if log_index >= event_log_index:
                continue
            from_address = contracts.word_to_address(log["topics"][1])
            to_address = contracts.word_to_address(log["topics"][2])
            amount = contracts.word_to_int(contracts.word_at(log["data"], 0))
            if from_address == affected:
                adjustment -= amount
            if to_address == affected:
                adjustment += amount
        return adjustment, len(logs)

    # ------------------------------------------------------------------- main
    def run(self) -> dict:
        """重建该交易（或指定日志）的事件表、余额表与验证表。

        同一交易内的多条事件日志各自成行，按 ``log_index`` 升序排列，互不合并：
        每条日志单独做状态机与余额查询，``event_class`` 由该日志的 topic0 唯一决定。
        """
        snapshot, snapshot_sha, logs = self._load_snapshot()
        events: list[dict] = []
        balances: list[dict] = []
        validation: list[dict] = []
        for position, log in enumerate(logs, start=1):
            event, event_balances, event_validation = self._build_event(
                snapshot, snapshot_sha, log, position, len(logs)
            )
            events.append(event)
            balances.extend(event_balances)
            validation.extend(event_validation)

        for index, row in enumerate(validation, start=1):
            row["validation_id"] = f"vd_{index:03d}"

        # 核对时刻取本表所用证据中最新的抓取时刻，使同一份缓存的输出逐字节稳定；
        # 局部检查（OFAC、字典）与不可用参照沿用该时刻。
        evidence = [
            row["reference_retrieved_at_utc"]
            for row in validation
            if row["reference_retrieved_at_utc"] and row["check_type"] != "schema_conformance"
        ]
        reviewed_at = max(evidence) if evidence else now_utc()
        for row in validation:
            if not row["reference_retrieved_at_utc"]:
                row["reference_retrieved_at_utc"] = reviewed_at
            row["reviewed_at_utc"] = reviewed_at

        return {"events": events, "balances": balances, "validation": validation}

    def _build_event(
        self,
        snapshot: dict,
        snapshot_sha: str,
        log: dict,
        position: int,
        event_count: int,
    ) -> tuple[dict, list[dict], list[dict]]:
        self.log_index = int(log["logIndex"], 16)
        topic0 = log["topics"][0].lower()
        spec = self.topics.get(topic0)
        if spec is None:
            raise SystemExit(f"topic0 不在事件表内: {topic0}")
        self.token_symbol = contracts.contract_for_address(
            self.cfg.registry, log["address"]
        )
        if self.token_symbol is None:
            raise SystemExit(f"日志来自未登记的合约: {log['address']}")
        if self.expected_token and self.token_symbol != self.expected_token:
            raise SystemExit(
                f"期望 {self.expected_token} 事件，快照命中的是 {self.token_symbol}"
            )
        self.token_contract = log["address"].lower()
        self.decimals = self.cfg.tokens[self.token_symbol]["decimals"]
        self.block_number = int(log["blockNumber"], 16)

        header = self._probe(
            "eth_getBlockByNumber", [hex(self.block_number), False]
        ).result
        if header["hash"].lower() != log["blockHash"].lower():
            raise SystemExit("日志 blockHash 与区块头不一致（非 canonical 链）")
        block_time_utc = rfc3339(int(header["timestamp"], 16))

        affected, decode_source, destruction_raw = contracts.decode_event_log(
            log, spec
        )
        identity = contracts.resolve_identity(
            self.rpc, self.cfg.registry, self.token_symbol, self.block_number
        )
        version = contracts.version_for_code_hash(
            self.cfg.registry, self.token_symbol, identity.contract_code_hash
        )
        abi_path = self.cfg.path(self.cfg.tokens[self.token_symbol]["abi_snapshot"])
        abi_sha = sha256_hex(abi_path.read_bytes())

        blacklist_fn = self.cfg.tokens[self.token_symbol]["blacklist_state_function"]
        state_before = self._state_call(
            blacklist_fn, affected, self.block_number - 1, "blacklist_state_before"
        )
        state_after = self._state_call(
            blacklist_fn, affected, self.block_number, "blacklist_state_after"
        )
        before_block_end = blacklist_state_label(state_before.result)
        after_block_end = blacklist_state_label(state_after.result)
        if self.block_events is None:
            self.block_events = build_block_events(
                snapshot, self.cfg.registry, self.topics
            )
        before, after = self._position_state(
            affected, before_block_end, spec.event_class
        )
        if before != before_block_end or after != after_block_end:
            self.notes.append(
                "blacklist_state_*: 采用事件位置状态"
                f"（before={before}, after={after}），"
                f"区块端点状态为 before={before_block_end}, after={after_block_end}"
            )

        balance_call = self._state_call(
            "balanceOf(address)", affected, self.block_number - 1, "pre_balance"
        )
        base_balance = contracts.word_to_int(contracts.word_at(balance_call.result, 0))
        adjustment, block_log_count = self._prior_transfer_adjustment(
            self.block_number, affected, "primary", self.log_index
        )
        pre_event_raw = base_balance + adjustment
        is_episode_start = spec.event_class == "freezing" and before == "not_blacklisted"

        prev_header = self._probe(
            "eth_getBlockByNumber", [hex(self.block_number - 1), False]
        ).result

        event_rows = [
            {
                "event_id": "ev_"
                + sha256_hex(
                    f"{self.cfg.chain['name']}|{log['transactionHash'].lower()}|{self.log_index}".encode()
                )[:32],
                "chain": self.cfg.chain["name"],
                "chain_id": str(self.cfg.chain["chain_id"]),
                "token_symbol": self.token_symbol,
                "token_contract": self.token_contract,
                "block_number": str(self.block_number),
                "block_hash": header["hash"].lower(),
                "block_time_utc": block_time_utc,
                "event_time_precision": "second",
                "tx_hash": log["transactionHash"].lower(),
                "tx_index": str(int(log["transactionIndex"], 16)),
                "log_index": str(self.log_index),
                "log_order_key": (
                    f"{self.block_number:010d}:{int(log['transactionIndex'], 16):04d}"
                    f":{self.log_index:06d}"
                ),
                "tx_event_count": str(
                    sum(
                        1
                        for other in snapshot["logs"]
                        if other["transactionHash"].lower()
                        == log["transactionHash"].lower()
                    )
                ),
                "event_topic0": topic0,
                "event_signature": spec.signature,
                "abi_source": contracts.abi_source(
                    abi_sha, self.cfg.rel(abi_path), version.label
                ),
                "raw_topics": canonical_json(log["topics"]),
                "raw_data": log["data"],
                "is_proxy": "true" if identity.is_proxy else "false",
                "proxy_address": identity.proxy_address,
                "implementation_address": identity.implementation_address,
                "contract_code_hash": identity.contract_code_hash,
                "contract_version_label": version.label,
                "contract_version_source": version.source,
                "contract_version_confidence": version.confidence,
                "event_class": spec.event_class,
                "affected_address": affected,
                "address_decode_source": decode_source,
                "blacklist_state_before": before,
                "blacklist_state_after": after,
                "blacklist_transition": "true" if before != after else "false",
                "destruction_amount_raw": destruction_raw or "",
                "destruction_amount_token": (
                    token_amount(destruction_raw, self.decimals)
                    if destruction_raw
                    else ""
                ),
                "token_decimals": str(self.decimals),
                "balance_pre_event_raw": str(pre_event_raw),
                "balance_pre_event_token": token_amount(pre_event_raw, self.decimals),
                "balance_pre_event_adjustment_raw": str(adjustment),
                "balance_pre_event_state_block": str(self.block_number - 1),
                "balance_pre_event_source": "archive_state_end_of_block",
                "balance_pre_event_precision": "exact_from_state_plus_transfers",
                "balance_query_id": balance_call.query_id,
                "source_endpoint_label": self.cfg.primary.label,
                "source_query": self._source_query(snapshot),
                "source_retrieved_at_utc": snapshot["retrieved_at_utc"],
                "raw_log_path": self.cfg.rel(self.cfg.log_snapshot),
                "raw_log_sha256": snapshot_sha,
                "dataset_contract_version": self.cfg.registry["dataset_contract_version"],
                "missingness_status": "complete",
                "missing_fields": "",
                "missing_reason_code": "",
                "verification_status": "pending",
                "analysis_eligibility": "core",
                "exclusion_rule_id": "",
                "exclusion_reason": "",
                "notes": "",
            }
        ]
        event = event_rows[0]
        if event_count == 1:
            event["notes"] = (
                f"single-event reconstruction; token event is log "
                f"{self.log_index} of the block; tx carries "
                f"{event['tx_event_count']} registered event log(s); "
                f"the block carries {block_log_count} Transfer log(s)"
            )
        else:
            event["notes"] = (
                f"multi-event reconstruction {position}/{event_count} "
                f"(ordered by log_order_key); token event is log "
                f"{self.log_index} of the block; tx carries "
                f"{event['tx_event_count']} registered event log(s); "
                f"the block carries {block_log_count} Transfer log(s)"
            )

        balances = self._balances(
            event, balance_call, base_balance, prev_header, is_episode_start
        )
        if not any(row["query_id"] == balance_call.query_id for row in balances):
            # 没有对应的 balances 行时不留悬空外键（R9 重复日志，或同一次 eth_call
            # 已按另一口径写入）；该次查询仍可在 data/raw/state_calls/ 追溯。
            event["balance_query_id"] = ""
        validation = self._validation(event, affected, pre_event_raw)
        event["verification_status"] = self._verification_status(validation)

        return event, balances, validation

    # ---------------------------------------------------------------- balances
    def _balances(
        self,
        event: dict,
        balance_call,
        base_balance: int,
        prev_header: dict,
        is_episode_start: bool,
    ) -> list[dict]:
        blacklist_fn = self.cfg.tokens[self.token_symbol]["blacklist_state_function"]
        rows: list[dict] = []

        if is_episode_start:
            balance_reference = "at_freeze"
            notes = (
                "D1 组成：STATE_AT_BLOCK_END(block-1) + 同区块更早 Transfer 调整量；"
                "本行 balance_raw 为该次 eth_call 原值，D1 合计见 events.balance_pre_event_raw"
            )
        elif event["event_class"] in {"unfreezing", "destruction"}:
            balance_reference = "at_event"
            notes = "事件前诊断值，不得当作 D1 报告"
        else:
            balance_reference = ""
            notes = "R9：重复 freezing 日志，不产生 D1，仅计入事件覆盖"

        if balance_reference:
            previous = self._emitted_query_ids.get(balance_call.query_id)
            if previous is not None:
                # balances 以 query_id 为主键：同一交易内两个事件若共用同一次
                # eth_call（同地址、同区块、同一口径），后一条不另起一行。
                self.notes.append(
                    f"balances: query_id={balance_call.query_id} 已按 {previous} 写入，"
                    f"{event['event_id']} 的 {balance_reference} 行不重复写入"
                )
                balance_reference = ""
        if balance_reference:
            self._emitted_query_ids[balance_call.query_id] = balance_reference
            rows.append(
                {
                    "query_id": balance_call.query_id,
                    "chain": self.cfg.chain["name"],
                    "token_symbol": self.token_symbol,
                    "token_contract": self.token_contract,
                    "address": event["affected_address"],
                    "balance_reference": balance_reference,
                    "reference_event_id": event["event_id"],
                    "balance_block_number": str(self.block_number - 1),
                    "balance_block_hash": prev_header["hash"].lower(),
                    "balance_raw": str(base_balance),
                    "balance_token": token_amount(base_balance, self.decimals),
                    "token_decimals": str(self.decimals),
                    "blacklist_state_at_balance_block": event["blacklist_state_before"],
                    "balance_source": "archive_eth_call",
                    "query_payload_sha256": balance_call.payload_sha256,
                    "queried_at_utc": balance_call.queried_at_utc,
                    "missingness_status": "complete",
                    "missing_reason_code": "",
                    "notes": notes,
                }
            )

        cutoff_key = (self.token_symbol, event["affected_address"])
        if cutoff_key in self._cutoff_done:
            return rows
        self._cutoff_done.add(cutoff_key)

        cutoff_state = self._state_call(
            blacklist_fn,
            event["affected_address"],
            self.cutoff_block,
            "blacklist_state_at_study_cutoff",
        )
        cutoff_label = blacklist_state_label(cutoff_state.result)
        if cutoff_label != "blacklisted":
            self.notes.append(
                f"D2: 地址在 study_cutoff_block={self.cutoff_block} 已不在黑名单集合，"
                "不产生 at_study_cutoff 余额行"
            )
            return rows

        cutoff_balance = self._state_call(
            "balanceOf(address)",
            event["affected_address"],
            self.cutoff_block,
            "balance_at_study_cutoff",
        )
        cutoff_header = self._probe(
            "eth_getBlockByNumber", [hex(self.cutoff_block), False]
        ).result
        cutoff_raw = str(
            contracts.word_to_int(contracts.word_at(cutoff_balance.result, 0))
        )
        if cutoff_balance.query_id in self._emitted_query_ids:
            # cutoff_block == block_number - 1 时截止日查询与事件前查询是同一次
            # eth_call；主键只能有一行，已在的那一行保留，这里不重复写入。
            self.notes.append(
                f"balances: query_id={cutoff_balance.query_id} 已按 "
                f"{self._emitted_query_ids[cutoff_balance.query_id]} 写入，"
                "at_study_cutoff 行不重复写入"
            )
            return rows
        self._emitted_query_ids[cutoff_balance.query_id] = "at_study_cutoff"
        rows.append(
            {
                "query_id": cutoff_balance.query_id,
                "chain": self.cfg.chain["name"],
                "token_symbol": self.token_symbol,
                "token_contract": self.token_contract,
                "address": event["affected_address"],
                "balance_reference": "at_study_cutoff",
                "reference_event_id": "",
                "balance_block_number": str(self.cutoff_block),
                "balance_block_hash": cutoff_header["hash"].lower(),
                "balance_raw": cutoff_raw,
                "balance_token": token_amount(cutoff_raw, self.decimals),
                "token_decimals": str(self.decimals),
                "blacklist_state_at_balance_block": cutoff_label,
                "balance_source": "archive_eth_call",
                "query_payload_sha256": cutoff_balance.payload_sha256,
                "queried_at_utc": cutoff_balance.queried_at_utc,
                "missingness_status": "complete",
                "missing_reason_code": "",
                "notes": "D2 研究截止日仍冻结余额",
            }
        )
        return rows

    # -------------------------------------------------------------- validation
    def _validation(
        self, event: dict, affected: str, pre_event_raw: int
    ) -> list[dict]:
        rows: list[dict] = []

        def add(
            check_type: str,
            field_name: str,
            contract_value: str,
            reference_value: str,
            reference_source: str,
            reference_at: str = "",
            deviation: str = "",
            reason: str = "",
            resolution: str = "",
            agreement: str | None = None,
        ) -> None:
            agreed = agreement
            if agreed is None:
                agreed = "match" if contract_value == reference_value else "mismatch"
                if agreed == "mismatch" and not reason:
                    reason = "unresolved"
            rows.append(
                {
                    "validation_id": f"vd_{len(rows) + 1:03d}",
                    "event_id": event["event_id"],
                    "check_type": check_type,
                    "field_name": field_name,
                    "contract_value": contract_value,
                    "reference_value": reference_value,
                    "reference_source": reference_source,
                    "reference_retrieved_at_utc": reference_at,
                    "agreement": agreed,
                    "deviation": deviation,
                    "deviation_reason": reason,
                    "resolution": resolution,
                    "reviewer": REVIEWER,
                    "reviewed_at_utc": "",
                }
            )

        # 1. position: per-event receipt (single/lifecycle mode), or the position
        # carried by one independent full-range log retrieval (core mode).
        if self.reference_positions is not None:
            reference_position = self.reference_positions.get(
                (event["tx_hash"], self.log_index), ""
            )
            if reference_position:
                add(
                    "source_position",
                    "log_order_key",
                    event["log_order_key"],
                    reference_position,
                    "独立端点全量 eth_getLogs 检索集合（同一检索用于全量覆盖）",
                    self.reference_retrieved_at,
                )
            else:
                add(
                    "source_position",
                    "log_order_key",
                    event["log_order_key"],
                    "absent from independent log retrieval",
                    "独立端点全量 eth_getLogs 检索集合",
                    self.reference_retrieved_at,
                    deviation="reference_missing",
                    reason="reference_log_absent",
                    resolution="R3：计入 coverage 与 unverifiable share，事件不剔除",
                    agreement="unverifiable",
                )
        else:
            try:
                receipt = self._probe(
                    "eth_getTransactionReceipt", [event["tx_hash"]], provider="reference"
                )
                receipt_result = receipt.result
                receipt_index = next(
                    (
                        int(log["logIndex"], 16)
                        for log in receipt_result["logs"]
                        if log["logIndex"] == hex(self.log_index)
                    ),
                    None,
                )
                reference_position = (
                    f"{int(receipt_result['blockNumber'], 16):010d}:"
                    f"{int(receipt_result['transactionIndex'], 16):04d}:{receipt_index:06d}"
                    if receipt_index is not None
                    else "log not present in receipt"
                )
                add(
                    "source_position",
                    "log_order_key",
                    event["log_order_key"],
                    reference_position,
                    f"{receipt.provider_label} eth_getTransactionReceipt (独立端点)",
                    receipt.retrieved_at_utc,
                )
            except (RpcError, KeyError, TypeError):
                add(
                    "source_position",
                    "log_order_key",
                    event["log_order_key"],
                    "reference_unavailable",
                    "独立端点不可用",
                    deviation="reference_missing",
                    reason="external_source_unavailable",
                    resolution="R3：计入 unverifiable share，不计为 match",
                    agreement="unverifiable",
                )

        # 2. contract version, from an independent provider's code + local hash
        if self.verify_contract_version:
            try:
                code_probe = self._probe(
                    "eth_getCode",
                    [event["implementation_address"], hex(self.block_number)],
                    provider=self.reference_state_provider,
                )
                reference_hash = contracts.keccak_hex(
                    bytes.fromhex(code_probe.result.removeprefix("0x"))
                )
                add(
                    "order_version",
                    "contract_code_hash",
                    event["contract_code_hash"],
                    reference_hash,
                    f"{code_probe.provider_label} eth_getCode at block "
                    f"{self.block_number} (独立端点)",
                    code_probe.retrieved_at_utc,
                )
            except RpcError:
                add(
                    "order_version",
                    "contract_code_hash",
                    event["contract_code_hash"],
                    "reference_unavailable",
                    "独立端点不可用",
                    deviation="reference_missing",
                    reason="external_source_unavailable",
                    resolution="R3",
                    agreement="unverifiable",
                )

        # 3. balance, re-derived by an independent provider from its own state + logs
        try:
            reference_request = {
                "to": self.token_contract,
                "data": contracts.encode_address_call("balanceOf(address)", affected),
            }
            reference_probe = self._probe(
                "eth_call",
                [reference_request, hex(self.block_number - 1)],
                provider=self.reference_state_provider,
            )
            reference_base = contracts.word_to_int(
                contracts.word_at(reference_probe.result, 0)
            )
            reference_adjustment, _ = self._prior_transfer_adjustment(
                self.block_number,
                affected,
                self.reference_state_provider,
                self.log_index,
            )
            add(
                "balance_state",
                "balance_pre_event_raw",
                str(pre_event_raw),
                str(reference_base + reference_adjustment),
                f"{reference_probe.provider_label} eth_call balanceOf at block-1 "
                "+ 同区块 Transfer 重算 (独立端点)",
                reference_probe.retrieved_at_utc,
            )
        except RpcError:
            add(
                "balance_state",
                "balance_pre_event_raw",
                str(pre_event_raw),
                "reference_unavailable",
                "独立端点不可用",
                deviation="reference_missing",
                reason="external_source_unavailable",
                resolution="R3",
                agreement="unverifiable",
            )

        # 4. public record match, from the local OFAC snapshot
        if self.ofac_index is not None:
            ofac_manifest = json.loads(
                (self.cfg.ofac_dir / "sdn_manifest.json").read_text(encoding="utf-8")
            )
            matched = self.ofac_index.get(affected.lower())
        else:
            index, ofac_manifest = load_ofac_index(self.cfg)
            matched = index.get(affected.lower())
        ofac_sha = ofac_manifest.get("csv_sha256", "")
        if matched:
            add(
                "manual_reference",
                "affected_address",
                affected,
                matched["address"].lower(),
                f"OFAC SDN snapshot uid={matched['uid']} "
                f"({ofac_sha[:8]}, publish {ofac_manifest.get('publish_date', 'n/a')}); "
                "公开记录字符串比对，人工签署待工单 04",
                ofac_manifest.get("retrieved_at_utc", ""),
                resolution="人工签署待评审；本次为公开记录比对",
            )
        else:
            add(
                "manual_reference",
                "affected_address",
                "unmatched",
                "absent from SDN ETH address set",
                "OFAC SDN snapshot; 公开记录字符串比对",
                ofac_manifest.get("retrieved_at_utc", ""),
                resolution="R6：无公开记录不推断未受制裁",
                agreement="match",
            )

        # 5. schema conformance of delivered tables against the dictionary
        # 全量模式下这条核对是整表级的，由 core 层只写一次，不随事件重复。
        if self.emit_schema_rows:
            add(
                "schema_conformance",
                "event_id",
                f"1 events row / {len(self.dictionary['events'])} columns; "
                "balances 与 validation 同法校验",
                "docs/data-dictionary.csv field list",
                "docs/data-dictionary.csv",
                resolution="列集合与字典一致，无字典外字段（deliver() 写出前断言）",
                agreement="match",
            )

        return rows

    @staticmethod
    def _verification_status(validation: list[dict]) -> str:
        agreements = {row["agreement"] for row in validation}
        if "mismatch" in agreements:
            return "pending"
        if "unverifiable" in agreements:
            return "unverifiable"
        return "verified"

    # --------------------------------------------------------------- delivery
    def deliver(self, result: dict) -> dict:
        events = result["events"]
        balances = result["balances"]
        validation = result["validation"]
        bundle = {"events": events, "balances": balances, "validation": validation}
        for table, rows in bundle.items():
            assert_columns(table, self.dictionary[table], rows)

        out_dir = self.cfg.out_root / events[0]["event_id"]
        written, parquet_written = write_bundle(out_dir, self.dictionary, bundle)

        manifest = {
            "reconstruction_id": "rs_"
            + sha256_hex(
                canonical_json(
                    {
                        "tx_hash": self.tx_hash,
                        "log_index": self.log_index,
                        "study_cutoff_block": self.cutoff_block,
                        "dataset_contract_version": self.cfg.registry[
                            "dataset_contract_version"
                        ],
                    }
                ).encode()
            )[:32],
            "dataset_contract_version": self.cfg.registry["dataset_contract_version"],
            "generated_at_utc": now_utc(),
            "inputs": {
                "log_snapshot": self.cfg.rel(self.cfg.log_snapshot),
                "log_snapshot_sha256": events[0]["raw_log_sha256"],
                "tx_hash": self.tx_hash,
                "log_index": self.log_index,
                "expected_token": self.expected_token,
                "study_cutoff_block": self.cutoff_block,
                "contract_registry": self.cfg.rel(self.cfg.contract_registry),
                "dictionary": self.cfg.rel(self.cfg.dictionary_csv),
            },
            "endpoints": {
                "primary_label": self.cfg.primary.label,
                "reference_labels": [
                    provider.label for provider in self.cfg.reference_providers
                ],
            },
            "balance_conventions": {
                "D1": "balance_at_freeze → balances.balance_reference = at_freeze",
                "D2": "balance_frozen_at_study_cutoff → balances.balance_reference = at_study_cutoff",
                "declared_for_this_record": [
                    row["balance_reference"] for row in balances
                ],
            },
            "counts": {
                "events": len(events),
                "balances": len(balances),
                "validation": len(validation),
                "validation_agreement": {
                    value: sum(1 for row in validation if row["agreement"] == value)
                    for value in sorted({row["agreement"] for row in validation})
                },
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
                    table: sorted(fields)
                    for table, fields in PROVENANCE_TIME_FIELDS.items()
                },
            },
            "notes": self.notes,
            "deviations": self.deviations,
            "reproduce_command": (
                "python scripts/reconstruct_single_event.py "
                f"--tx-hash {self.tx_hash} --log-index {self.log_index} "
                f"--cutoff-block {self.cutoff_block}"
            ),
            "outputs": {
                table: self.cfg.rel(out_dir / f"{table}.csv")
                for table in ("events", "balances", "validation")
            },
        }
        manifest_path = out_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        manifest["manifest_path"] = self.cfg.rel(manifest_path)
        return manifest
