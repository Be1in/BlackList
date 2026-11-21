"""Representative-sample validation and the study-scope decision check (ticket 04).

The sample is the union of every case declared in ``config/lifecycle_cases.json``
(which carries the six manually checked ticket-01 slots), so one run exercises the
whole chain: USDT and USDC, freezing / unfreezing / destruction, a multi-log
transaction, a contract upgrade, and both balance conventions.

This module does not re-implement parsing. It runs ``CaseRun`` (ticket 03), merges
the outputs, adds the checks that only make sense at sample scope -- independent
log retrieval, D2 (cut-off) balances and D2 membership, destruction amount,
canonical cut-off block -- and derives the three headline metrics:

* ``event_retrieval_coverage``: logs retrieved in the sample blocks divided by the
  independent provider's log total for the same token/topic/block (slot-level
  counts are reported beside it).
* ``balance_reconstruction_error``: reconstructed vs. independently re-derived
  balances, by reference (D1 / at_event / D2).
* ``unverifiable_share``: share of validation rows with no independent reference.

The study period is *not* decided here. The run writes the metrics; the decision
lives in ``config/study_scope.json`` and is checked against the written manifest by
``check_scope`` (which also re-derives the data digest from the delivered CSVs).
That split is what makes "validation first, scope second" auditable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from . import contracts
from .config import Config
from .lifecycle import (
    CASES_PATH,
    CaseRun,
    load_cases,
    merge_bundles,
    validation_row,
)
from .pipeline import blacklist_state_label
from .rpc import RpcCache, RpcError, canonical_json, now_utc, sha256_hex
from .tables import (
    assert_columns,
    artifact_digest,
    data_digest,
    load_dictionary,
    ratio,
    read_table,
    write_bundle,
)

SAMPLE_RECORDS = "data/raw/sample_records.json"
SCOPE_PATH = "config/study_scope.json"

DECISION_VERSION = "v1"
TABLE_ORDER = ("events", "balances", "validation")


def _is_int(value: object) -> bool:
    text = str(value).strip()
    return text.isdigit() or (text.startswith("-") and text[1:].isdigit())


def _resolve(payload: dict, dotted: str) -> object:
    node: object = payload
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            raise KeyError(dotted)
        node = node[part]
    return node


def sample_records(path: Path) -> tuple[dict, str]:
    raw = path.read_bytes()
    return json.loads(raw.decode("utf-8")), sha256_hex(raw)


@dataclass
class SampleValidation:
    cfg: Config
    cutoff_block: int
    sample_path: str = SAMPLE_RECORDS
    cases_path: str = CASES_PATH
    scope_path: str = SCOPE_PATH
    dictionary: dict[str, list[str]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    deviations: list[dict] = field(default_factory=list)
    cache_stats: dict[str, int] = field(
        default_factory=lambda: {"state_calls_from_cache": 0, "probes_from_cache": 0}
    )
    errors: dict[str, dict] = field(default_factory=dict)
    coverage: dict = field(default_factory=dict)
    cutoff_block_hash: str = ""
    finalized_head_block: int = 0

    def __post_init__(self) -> None:
        self.rpc = RpcCache(self.cfg)
        self.topics = contracts.verify_topic_table(self.cfg.registry)
        self.dictionary = load_dictionary(self.cfg.dictionary_csv)

    # ------------------------------------------------------------------ helpers
    def _probe(self, method: str, params: list, provider: str = "primary"):
        probe = self.rpc.probe(method, params, provider=provider)
        self.cache_stats["probes_from_cache"] += int(probe.from_cache)
        return probe

    def _state_call(
        self, token: str, signature: str, address: str, block_number: int, label: str
    ):
        call = self.rpc.state_call(
            to=self.cfg.tokens[token]["contract"].lower(),
            data=contracts.encode_address_call(signature, address),
            block_number=block_number,
            label=label,
        )
        self.cache_stats["state_calls_from_cache"] += int(call.from_cache)
        return call

    def _record_error(
        self, bucket: str, contract_value: object, reference_value: object, decimals: int
    ) -> None:
        stats = self.errors.setdefault(
            bucket,
            {
                "checks": 0,
                "exact": 0,
                "sum_abs_raw": 0,
                "max_abs_raw": 0,
                "max_abs_token": Decimal(0),
            },
        )
        if not _is_int(contract_value) or not _is_int(reference_value):
            return
        error = abs(int(str(contract_value)) - int(str(reference_value)))
        stats["checks"] += 1
        stats["exact"] += int(error == 0)
        stats["sum_abs_raw"] += error
        stats["max_abs_raw"] = max(stats["max_abs_raw"], error)
        stats["max_abs_token"] = max(
            stats["max_abs_token"], Decimal(error) / (Decimal(10) ** decimals)
        )

    # ------------------------------------------------------------- run the chain
    def run(self) -> dict:
        sample, sample_sha = sample_records(self.cfg.path(self.sample_path))
        cases, cases_rel = load_cases(self.cfg, self.cases_path)
        cases_sha = sha256_hex(self.cfg.path(self.cases_path).read_bytes())

        outcomes: list[dict] = []
        for case_id, case in cases["cases"].items():
            run = CaseRun(
                cfg=self.cfg,
                case_id=case_id,
                case=case,
                cutoff_block=self.cutoff_block,
                cases_path=cases_rel,
            )
            outcomes.append(run.run())
            self.notes.extend(run.notes)
            self.deviations.extend(run.deviations)
            for key, value in run.cache_stats.items():
                self.cache_stats[key] += value

        merged = merge_bundles([outcome["bundle"] for outcome in outcomes])
        events = merged["events"]
        balances = merged["balances"]
        validation = merged["validation"]
        by_event = {event["event_id"]: event for event in events}

        self._collect_pre_event_errors(validation, by_event)
        validation.extend(self._destruction_rows(events, validation))
        validation.extend(self._retrieval_rows(events, sample))
        validation.extend(self._cutoff_rows(balances, sample))
        validation.extend(self._cutoff_block_rows(balances))

        for index, row in enumerate(validation, start=1):
            row["validation_id"] = f"vd_{index:03d}"
        evidence = [
            row["reference_retrieved_at_utc"]
            for row in validation
            if row["reference_retrieved_at_utc"]
            and row["check_type"] != "schema_conformance"
        ]
        reviewed_at = max(evidence) if evidence else now_utc()
        for row in validation:
            if not row["reference_retrieved_at_utc"]:
                row["reference_retrieved_at_utc"] = reviewed_at
            row["reviewed_at_utc"] = reviewed_at

        bundle = {"events": events, "balances": balances, "validation": validation}
        return {
            "bundle": bundle,
            "sample_sha256": sample_sha,
            "cases_sha256": cases_sha,
            "cases_path": cases_rel,
            "scope_inputs": self._scope_inputs(sample),
            "metrics": self._metrics(bundle),
        }

    # ---------------------------------------------------- pre-event (D1/at_event)
    def _collect_pre_event_errors(
        self, validation: list[dict], by_event: dict[str, dict]
    ) -> None:
        """每个事件第一条 ``balance_pre_event_raw`` 核对行即流水线的独立重算行。

        ``Reconstruction`` 为每个事件先写 5 条核对，其中第 3 条是余额；案例层随后
        追加的样本对照行也复用同一字段名，因此按"首次出现"取值，避免重复计数。
        """
        seen: set[str] = set()
        for row in validation:
            if row["check_type"] != "balance_state":
                continue
            if row["field_name"] != "balance_pre_event_raw":
                continue
            event_id = row["event_id"]
            if not event_id or event_id in seen:
                continue
            event = by_event.get(event_id)
            if event is None:
                continue
            seen.add(event_id)
            if event["event_class"] == "freezing":
                if event["blacklist_state_before"] != "not_blacklisted":
                    # R9：重复 freezing 日志不产生 D1，也不进入误差统计。
                    continue
                bucket = "at_freeze"
            elif event["event_class"] in {"unfreezing", "destruction"}:
                bucket = "at_event"
            else:
                continue
            self._record_error(
                bucket,
                row["contract_value"],
                row["reference_value"],
                int(event["token_decimals"]),
            )

    # ------------------------------------------------------ destruction amount
    def _destruction_rows(self, events: list[dict], validation: list[dict]) -> list[dict]:
        reference: dict[str, str] = {}
        for row in validation:
            if (
                row["check_type"] == "balance_state"
                and row["field_name"] == "balance_pre_event_raw"
                and row["event_id"]
                and row["event_id"] not in reference
            ):
                reference[row["event_id"]] = row["reference_value"]

        rows: list[dict] = []
        for event in events:
            if event["event_class"] != "destruction":
                continue
            expected = reference.get(event["event_id"], "")
            if not _is_int(expected):
                rows.append(
                    validation_row(
                        "balance_state",
                        "destruction_amount_raw",
                        event["destruction_amount_raw"],
                        expected or "reference_unavailable",
                        "独立端点重算的事件前余额（同事件 balance_state 行不可用）",
                        event_id=event["event_id"],
                        deviation="reference_missing",
                        reason="external_source_unavailable",
                        resolution="R3：计入 unverifiable share，不计为 match",
                        agreement="unverifiable",
                    )
                )
                continue
            rows.append(
                validation_row(
                    "balance_state",
                    "destruction_amount_raw",
                    event["destruction_amount_raw"],
                    expected,
                    "独立端点按同一规则重算的事件前余额（同事件 balance_state 行）",
                    event_id=event["event_id"],
                    resolution=(
                        "USDT 销毁不 emit Transfer 到零地址，金额只能取自事件参数；"
                        "销毁金额应等于销毁前一刻余额"
                    ),
                )
            )
            self._record_error(
                "destruction_amount",
                event["destruction_amount_raw"],
                expected,
                int(event["token_decimals"]),
            )
        return rows

    # ------------------------------------------------- independent log retrieval
    def _retrieval_rows(self, events: list[dict], sample: dict) -> list[dict]:
        event_index = {
            (event["tx_hash"], int(event["log_index"])): event for event in events
        }
        snapshot = json.loads(
            self.cfg.log_snapshot.read_text(encoding="utf-8")
        )
        # 覆盖按「同一代币、同一 topic、同一区块的日志集合」计算：分母来自独立端点，
        # 与 definitions.md §7 的「分母由区块范围内日志总数给出」一致；槽位计数另报。
        snapshot_keys: dict[tuple[str, str, int], set[tuple[str, int]]] = {}
        for log in snapshot["logs"]:
            token = contracts.contract_for_address(self.cfg.registry, log["address"])
            key = (
                token or "unknown",
                log["topics"][0].lower(),
                int(log["blockNumber"], 16),
            )
            snapshot_keys.setdefault(key, set()).add(
                (log["transactionHash"].lower(), int(log["logIndex"], 16))
            )

        coverage: dict = {
            "declared": 0,
            "reconstructed": 0,
            "covered": 0,
            "reference_found": 0,
            "expected_events": 0,
            "retrieved_events": 0,
            "matched_events": 0,
            "snapshot_only_events": 0,
            "reference_only_events": 0,
            "blocks": 0,
            "parity": 0,
            "by_token": {},
        }
        rows: list[dict] = []
        seen_blocks: set[tuple[str, str, int]] = set()

        for record in sample.get("records", []):
            declared = record["event"]
            token = declared["token"]
            block = int(declared["block"])
            topic0 = declared["topic0"].lower()
            key = (token, topic0, block)
            bucket = coverage["by_token"].setdefault(
                token,
                {
                    "declared": 0,
                    "reconstructed": 0,
                    "covered": 0,
                    "reference_found": 0,
                    "expected_events": 0,
                    "retrieved_events": 0,
                    "matched_events": 0,
                },
            )
            coverage["declared"] += 1
            bucket["declared"] += 1

            event = event_index.get(
                (declared["tx"].lower(), int(declared["log_index"]))
            )
            if event is not None:
                coverage["reconstructed"] += 1
                bucket["reconstructed"] += 1
            contract_value = event["log_index"] if event is not None else "not_reconstructed"

            try:
                probe = self._probe(
                    "eth_getLogs",
                    [
                        {
                            "address": self.cfg.tokens[token]["contract"].lower(),
                            "fromBlock": hex(block),
                            "toBlock": hex(block),
                            "topics": [topic0],
                        }
                    ],
                    provider="reference",
                )
                logs = probe.result
                reference_at = probe.retrieved_at_utc
                hit = next(
                    (
                        log
                        for log in logs
                        if log["transactionHash"].lower() == declared["tx"].lower()
                        and int(log["logIndex"], 16) == int(declared["log_index"])
                    ),
                    None,
                )
                reference_count = len(logs)
            except RpcError as exc:
                rows.append(
                    validation_row(
                        "source_position",
                        "log_index",
                        contract_value,
                        "reference_unavailable",
                        f"独立端点 eth_getLogs 不可用：{exc}",
                        deviation="reference_missing",
                        reason="external_source_unavailable",
                        resolution="R3：计入 unverifiable share，不计为 match",
                        agreement="unverifiable",
                    )
                )
                continue

            if hit is not None:
                coverage["reference_found"] += 1
                bucket["reference_found"] += 1
                if event is not None:
                    coverage["covered"] += 1
                    bucket["covered"] += 1
                reference_value = str(int(hit["logIndex"], 16))
                deviation = ""
                reason = ""
            else:
                reference_value = "absent_from_reference_logs"
                deviation = "event_not_in_reference_logs"
                reason = "independent_provider_returned_other_logs"

            rows.append(
                validation_row(
                    "source_position",
                    "log_index",
                    contract_value,
                    reference_value,
                    f"{probe.provider_label} eth_getLogs(address={token}, block {block}, "
                    f"topic0={topic0[:10]}…) 按 tx+log_index 定位",
                    reference_at=reference_at,
                    deviation=deviation,
                    reason=reason,
                    resolution=(
                        f"样本槽位 {record['slot']}：检索覆盖核对；"
                        "事件应同时出现在快照与独立端点的同区块日志中"
                    ),
                )
            )

            if key in seen_blocks:
                continue
            seen_blocks.add(key)
            reference_keys = {
                (log["transactionHash"].lower(), int(log["logIndex"], 16))
                for log in logs
            }
            retrieved_keys = snapshot_keys.get(key, set())
            matched_keys = reference_keys & retrieved_keys
            snapshot_only = len(retrieved_keys - reference_keys)
            reference_only = len(reference_keys - retrieved_keys)
            coverage["expected_events"] += len(reference_keys)
            coverage["retrieved_events"] += len(retrieved_keys)
            coverage["matched_events"] += len(matched_keys)
            coverage["snapshot_only_events"] += snapshot_only
            coverage["reference_only_events"] += reference_only
            bucket["expected_events"] += len(reference_keys)
            bucket["retrieved_events"] += len(retrieved_keys)
            bucket["matched_events"] += len(matched_keys)
            snapshot_count = len(retrieved_keys)
            reference_count = len(reference_keys)
            parity_ok = snapshot_only == 0 and reference_only == 0
            coverage["blocks"] += 1
            if parity_ok:
                coverage["parity"] += 1
            rows.append(
                validation_row(
                    "source_position",
                    "block_number",
                    f"{block}:{snapshot_count}",
                    f"{block}:{reference_count}",
                    f"{probe.provider_label} eth_getLogs 同区块同 topic 的日志条数",
                    reference_at=reference_at,
                    deviation="" if parity_ok else "log_set_differs",
                    reason="" if parity_ok else "retrieval_gap_or_duplicate",
                    resolution=(
                        f"样本槽位 {record['slot']}：区块级检索一致性"
                        "（快照条数 vs 独立端点条数）"
                    ),
                )
            )

        self.coverage = coverage
        return rows

    # ------------------------------------------------------- D2 (cut-off) checks
    def _cutoff_rows(self, balances: list[dict], sample: dict) -> list[dict]:
        cutoff_by_key = {
            (row["token_symbol"], row["address"]): row
            for row in balances
            if row["balance_reference"] == "at_study_cutoff"
        }
        seen: set[tuple[str, str]] = set()
        rows: list[dict] = []
        for record in sample.get("records", []):
            token = record["event"]["token"]
            address = record["event"]["address"].lower()
            key = (token, address)
            if key in seen:
                continue
            seen.add(key)

            state_fn = self.cfg.tokens[token]["blacklist_state_function"]
            state_call = self._state_call(
                token, state_fn, address, self.cutoff_block, "sample_cutoff_state"
            )
            label = blacklist_state_label(state_call.result)

            try:
                request = {
                    "to": self.cfg.tokens[token]["contract"].lower(),
                    "data": contracts.encode_address_call(state_fn, address),
                }
                probe = self._probe(
                    "eth_call", [request, hex(self.cutoff_block)], provider="reference"
                )
                reference_label = blacklist_state_label(probe.result)
                reference_at = probe.retrieved_at_utc
            except RpcError as exc:
                rows.append(
                    validation_row(
                        "balance_state",
                        "balance_reference",
                        "at_study_cutoff" if label == "blacklisted" else "none",
                        "reference_unavailable",
                        f"独立端点截止日状态查询不可用：{exc}",
                        deviation="reference_missing",
                        reason="external_source_unavailable",
                        resolution="R3：计入 unverifiable share，不计为 match",
                        agreement="unverifiable",
                    )
                )
                continue

            rows.append(
                validation_row(
                    "balance_state",
                    "balance_reference",
                    "at_study_cutoff" if label == "blacklisted" else "none",
                    "at_study_cutoff" if reference_label == "blacklisted" else "none",
                    f"{probe.provider_label} eth_call {state_fn} at "
                    f"study_cutoff_block={self.cutoff_block}",
                    reference_at=reference_at,
                    resolution=(
                        "D2 总体归属：截止日黑名单状态决定是否产生 at_study_cutoff 行；"
                        "解冻后未再冻结的地址不进入该总体"
                    ),
                )
            )

            row = cutoff_by_key.get(key)
            if row is None:
                continue
            try:
                balance_probe = self._probe(
                    "eth_call",
                    [
                        {
                            "to": self.cfg.tokens[token]["contract"].lower(),
                            "data": contracts.encode_address_call(
                                "balanceOf(address)", address
                            ),
                        },
                        hex(self.cutoff_block),
                    ],
                    provider="reference",
                )
                reference_balance = str(
                    contracts.word_to_int(
                        contracts.word_at(balance_probe.result, 0)
                    )
                )
                reference_at = balance_probe.retrieved_at_utc
            except RpcError as exc:
                rows.append(
                    validation_row(
                        "balance_state",
                        "balance_raw",
                        row["balance_raw"],
                        "reference_unavailable",
                        f"独立端点截止日余额查询不可用：{exc}",
                        deviation="reference_missing",
                        reason="external_source_unavailable",
                        resolution="R3",
                        agreement="unverifiable",
                    )
                )
                continue

            rows.append(
                validation_row(
                    "balance_state",
                    "balance_raw",
                    row["balance_raw"],
                    reference_balance,
                    f"{balance_probe.provider_label} eth_call balanceOf at "
                    f"study_cutoff_block={self.cutoff_block}",
                    reference_at=reference_at,
                    resolution="D2 独立复核：截止日仍冻结余额",
                )
            )
            self._record_error(
                "at_study_cutoff",
                row["balance_raw"],
                reference_balance,
                int(row["token_decimals"]),
            )
        return rows

    def _cutoff_block_rows(self, balances: list[dict]) -> list[dict]:
        cutoff_rows = [
            row for row in balances if row["balance_reference"] == "at_study_cutoff"
        ]
        rows: list[dict] = []

        if cutoff_rows:
            contract_hash = cutoff_rows[0]["balance_block_hash"]
            try:
                probe = self._probe(
                    "eth_getBlockByNumber",
                    [hex(self.cutoff_block), False],
                    provider="reference",
                )
                reference_hash = probe.result["hash"].lower()
                reference_at = probe.retrieved_at_utc
            except (RpcError, KeyError, TypeError) as exc:
                rows.append(
                    validation_row(
                        "balance_state",
                        "balance_block_hash",
                        contract_hash,
                        "reference_unavailable",
                        f"独立端点截止日区块头不可用：{exc}",
                        deviation="reference_missing",
                        reason="external_source_unavailable",
                        resolution="R3",
                        agreement="unverifiable",
                    )
                )
            else:
                self.cutoff_block_hash = reference_hash
                rows.append(
                    validation_row(
                        "balance_state",
                        "balance_block_hash",
                        contract_hash,
                        reference_hash,
                        f"独立端点 eth_getBlockByNumber at "
                        f"study_cutoff_block={self.cutoff_block}",
                        reference_at=reference_at,
                        resolution=(
                            "截止日余额查询落在 canonical 区块上（与独立端点区块头一致）"
                        ),
                    )
                )

        # 截止日按区块高度定义；这里验证它不高于验证时的 finalized 头，
        # 使"最后一个已 finalize 的区块"这条契约在产物里可查。
        try:
            finalized = self._probe(
                "eth_getBlockByNumber", ["finalized", False], provider="primary"
            )
            finalized_block = int(finalized.result["number"], 16)
            finalized_at = finalized.retrieved_at_utc
        except (RpcError, KeyError, TypeError, ValueError) as exc:
            rows.append(
                validation_row(
                    "source_position",
                    "block_number",
                    str(self.cutoff_block),
                    "finalized_head_unavailable",
                    f"采集端点 eth_getBlockByNumber(finalized) 不可用：{exc}",
                    deviation="reference_missing",
                    reason="external_source_unavailable",
                    resolution="R3：截止日的 finality 无法在本次运行中确认",
                    agreement="unverifiable",
                )
            )
        else:
            self.finalized_head_block = finalized_block
            finalized_enough = self.cutoff_block <= finalized_block
            rows.append(
                validation_row(
                    "source_position",
                    "block_number",
                    str(self.cutoff_block),
                    f"finalized_head={finalized_block}",
                    "采集端点 eth_getBlockByNumber(finalized)",
                    reference_at=finalized_at,
                    deviation="" if finalized_enough else "cutoff_above_finalized_head",
                    reason=""
                    if finalized_enough
                    else "study_cutoff_not_finalized",
                    resolution=(
                        "截止区块不高于验证时的 finalized 头；其 canonical 区块头见 "
                        "balance_block_hash 行"
                    ),
                    agreement="match" if finalized_enough else "mismatch",
                )
            )
        return rows

    # --------------------------------------------------------------- metrics
    def _metrics(self, bundle: dict) -> dict:
        coverage = self.coverage or {
            "declared": 0,
            "reconstructed": 0,
            "covered": 0,
            "reference_found": 0,
            "expected_events": 0,
            "retrieved_events": 0,
            "matched_events": 0,
            "snapshot_only_events": 0,
            "reference_only_events": 0,
            "blocks": 0,
            "parity": 0,
            "by_token": {},
        }
        tokens: dict[str, dict] = {}
        for token, bucket in coverage["by_token"].items():
            tokens[token] = {
                "declared_slots": bucket["declared"],
                "slots_covered": bucket["covered"],
                "expected_events": bucket["expected_events"],
                "retrieved_events": bucket["retrieved_events"],
                "matched_events": bucket["matched_events"],
                "coverage": ratio(bucket["matched_events"], bucket["expected_events"]),
            }

        matrix: dict[str, list[str]] = {}
        for row in bundle["events"]:
            classes = matrix.setdefault(row["token_symbol"], [])
            if row["event_class"] not in classes:
                classes.append(row["event_class"])
        versions = sorted(
            {
                row["contract_version_label"]
                for row in bundle["events"]
                if row["contract_version_label"]
            }
        )

        validation = bundle["validation"]
        by_check: dict[str, dict] = {}
        for row in validation:
            bucket = by_check.setdefault(
                row["check_type"], {"rows": 0, "unverifiable": 0}
            )
            bucket["rows"] += 1
            bucket["unverifiable"] += int(row["agreement"] == "unverifiable")
        unverifiable = sum(1 for row in validation if row["agreement"] == "unverifiable")
        for bucket in by_check.values():
            bucket["share"] = ratio(bucket["unverifiable"], bucket["rows"])

        by_reference: dict[str, dict] = {}
        total_checks = total_exact = total_sum = total_max = 0
        max_token = Decimal(0)
        for bucket, stats in sorted(self.errors.items()):
            by_reference[bucket] = {
                "checks": stats["checks"],
                "exact_matches": stats["exact"],
                "max_abs_error_raw": str(stats["max_abs_raw"]),
                "mean_abs_error_raw": (
                    format(
                        Decimal(stats["sum_abs_raw"]) / stats["checks"], ".6f"
                    )
                    if stats["checks"]
                    else "0.000000"
                ),
                "max_abs_error_token": format(stats["max_abs_token"], ".6f"),
            }
            total_checks += stats["checks"]
            total_exact += stats["exact"]
            total_sum += stats["sum_abs_raw"]
            total_max = max(total_max, stats["max_abs_raw"])
            max_token = max(max_token, stats["max_abs_token"])

        return {
            "scope": (
                "代表性样本；全量 event retrieval coverage 属工单 05，"
                "本指标只覆盖配置中声明的样本槽位与其所在区块"
            ),
            "event_retrieval_coverage": {
                "coverage": ratio(
                    coverage["matched_events"], coverage["expected_events"]
                ),
                "expected_events": coverage["expected_events"],
                "retrieved_events": coverage["retrieved_events"],
                "matched_events": coverage["matched_events"],
                "snapshot_only_events": coverage["snapshot_only_events"],
                "reference_only_events": coverage["reference_only_events"],
                "declared_sample_slots": coverage["declared"],
                "slots_reconstructed": coverage["reconstructed"],
                "slots_found_in_reference_logs": coverage["reference_found"],
                "slots_covered": coverage["covered"],
                "block_level_log_parity": {
                    "blocks": coverage["blocks"],
                    "matched": coverage["parity"],
                },
                "by_token": tokens,
                "denominator": (
                    "样本槽位所在区块内该代币该 topic 的独立端点日志总数"
                    "（definitions.md §7）；槽位计数另报；多事件交易在本范围结构性不可得"
                ),
            },
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
                "by_reference": by_reference,
            },
            "unverifiable_share": {
                "validation_rows": len(validation),
                "unverifiable": unverifiable,
                "share": ratio(unverifiable, len(validation)),
                "by_check_type": by_check,
            },
            "sample_matrix": {
                "tokens": sorted(matrix),
                "event_classes_by_token": {
                    token: classes for token, classes in sorted(matrix.items())
                },
                "contract_versions": versions,
            },
        }

    # ---------------------------------------------------------- scope inputs
    def _scope_inputs(self, sample: dict) -> dict:
        snapshot = json.loads(self.cfg.log_snapshot.read_text(encoding="utf-8"))
        first_block: dict[str, int] = {}
        counts: dict[str, dict[str, int]] = {}
        tx_counts: dict[str, int] = {}
        for log in snapshot["logs"]:
            token = contracts.contract_for_address(self.cfg.registry, log["address"])
            if token is None:
                continue
            block = int(log["blockNumber"], 16)
            first_block[token] = min(first_block.get(token, block), block)
            spec = self.topics.get(log["topics"][0].lower())
            event_class = spec.event_class if spec else "unknown"
            counts.setdefault(token, {})
            counts[token][event_class] = counts[token].get(event_class, 0) + 1
            tx = log["transactionHash"].lower()
            tx_counts[tx] = tx_counts.get(tx, 0) + 1

        slots_by_token: dict[str, list[str]] = {}
        for record in sample.get("records", []):
            slots_by_token.setdefault(record["event"]["token"], []).append(record["slot"])

        return {
            "snapshot_start_block": snapshot["start_block"],
            "snapshot_end_block": snapshot["end_block"],
            "total_registered_events": len(snapshot["logs"]),
            "txs_with_multiple_events": sum(
                1 for count in tx_counts.values() if count > 1
            ),
            "first_event_block_by_token": dict(sorted(first_block.items())),
            "event_counts_by_token_and_class": {
                token: dict(sorted(classes.items()))
                for token, classes in sorted(counts.items())
            },
            "declared_sample_slots": [
                record["slot"] for record in sample.get("records", [])
            ],
            "declared_sample_slots_by_token": {
                token: slots for token, slots in sorted(slots_by_token.items())
            },
        }

    # --------------------------------------------------------------- delivery
    def run_id(self, sample_sha: str, cases_sha: str) -> str:
        snapshot_sha = sha256_hex(self.cfg.log_snapshot.read_bytes())
        return "vs_" + sha256_hex(
            canonical_json(
                {
                    "sample_records_sha256": sample_sha,
                    "cases_sha256": cases_sha,
                    "log_snapshot_sha256": snapshot_sha,
                    "study_cutoff_block": self.cutoff_block,
                    "dataset_contract_version": self.cfg.registry[
                        "dataset_contract_version"
                    ],
                }
            ).encode()
        )[:32]

    def deliver(self, outcome: dict) -> dict:
        bundle = outcome["bundle"]
        for table, rows in bundle.items():
            assert_columns(table, self.dictionary[table], rows)

        run_id = self.run_id(outcome["sample_sha256"], outcome["cases_sha256"])
        out_dir = self.cfg.out_root / run_id
        written, parquet_written = write_bundle(out_dir, self.dictionary, bundle)

        metrics = outcome["metrics"]
        manifest = {
            "validation_id": run_id,
            "dataset_contract_version": self.cfg.registry["dataset_contract_version"],
            "generated_at_utc": now_utc(),
            "inputs": {
                "sample_records": self.cfg.rel(self.cfg.path(self.sample_path)),
                "sample_records_sha256": outcome["sample_sha256"],
                "cases": outcome["cases_path"],
                "cases_sha256": outcome["cases_sha256"],
                "log_snapshot": self.cfg.rel(self.cfg.log_snapshot),
                "log_snapshot_sha256": bundle["events"][0]["raw_log_sha256"],
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
            "counts": {
                "events": len(bundle["events"]),
                "balances": len(bundle["balances"]),
                "validation": len(bundle["validation"]),
                "event_class": {
                    value: sum(
                        1 for row in bundle["events"] if row["event_class"] == value
                    )
                    for value in sorted(
                        {row["event_class"] for row in bundle["events"]}
                    )
                },
                "balance_reference": {
                    value: sum(
                        1
                        for row in bundle["balances"]
                        if row["balance_reference"] == value
                    )
                    for value in sorted(
                        {row["balance_reference"] for row in bundle["balances"]}
                    )
                },
                "validation_agreement": {
                    value: sum(
                        1 for row in bundle["validation"] if row["agreement"] == value
                    )
                    for value in sorted(
                        {row["agreement"] for row in bundle["validation"]}
                    )
                },
            },
            "metrics": metrics,
            "scope_inputs": {
                **outcome["scope_inputs"],
                "cutoff_block_hash": self.cutoff_block_hash,
                "finalized_head_block": self.finalized_head_block,
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
            },
            "notes": self.notes,
            "deviations": self.deviations,
            "reproduce_command": (
                "python scripts/validate_sample.py "
                f"--cutoff-block {self.cutoff_block} "
                f"--sample {self.cfg.rel(self.cfg.path(self.sample_path))} "
                f"--cases {outcome['cases_path']} "
                f"--out-root {self.cfg.rel(self.cfg.out_root)}"
            ),
            "scope_check_command": "python scripts/validate_sample.py --check-scope",
            "outputs": {
                table: self.cfg.rel(out_dir / f"{table}.csv") for table in TABLE_ORDER
            },
        }
        manifest_path = out_dir / "manifest.json"
        if manifest_path.exists():
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
            unchanged = (
                previous.get("digests", {}).get("data_digest")
                == manifest["digests"]["data_digest"]
            )
            if unchanged and previous.get("generated_at_utc"):
                # 取证时间取"证据首次产生"的时刻：重放同一输入不改变它，
                # 否则任何一次复跑都会让「验证在前、决策在后」的校验失效。
                manifest["generated_at_utc"] = previous["generated_at_utc"]
        manifest["last_verified_at_utc"] = now_utc()
        manifest_path.write_text(
            json.dumps(manifest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        manifest["manifest_path"] = self.cfg.rel(manifest_path)
        return manifest

    # ---------------------------------------------------------- scope decision
    def check_scope(self, sample_path: str | None = None) -> list[str]:
        """Verify the written validation artifact and the study-scope decision.

        No network access: the manifest names the delivered CSVs, their data digest
        is recomputed from those files, and the decision must cite that digest and
        a ``decided_at_utc`` no earlier than the run's ``generated_at_utc``.
        """
        sample, sample_sha = sample_records(
            self.cfg.path(sample_path or self.sample_path)
        )
        cases_sha = sha256_hex(self.cfg.path(self.cases_path).read_bytes())
        run_id = self.run_id(sample_sha, cases_sha)
        out_dir = self.cfg.out_root / run_id
        manifest_path = out_dir / "manifest.json"
        scope_path = self.cfg.path(self.scope_path)

        problems: list[str] = []
        if not manifest_path.exists():
            return [f"验证产物缺失：{self.cfg.rel(manifest_path)}"]
        if not scope_path.exists():
            return [f"范围决策缺失：{self.cfg.rel(scope_path)}"]

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        decision = json.loads(scope_path.read_text(encoding="utf-8"))

        if manifest.get("validation_id") != run_id:
            problems.append(
                f"manifest.validation_id={manifest.get('validation_id')} 与输入派生的 {run_id} 不一致"
            )

        delivered = {
            table: read_table(out_dir / f"{table}.csv") for table in TABLE_ORDER
        }
        for table, rows in delivered.items():
            fields = self.dictionary[table]
            missing = [row for row in rows if set(row) != set(fields)]
            if missing:
                problems.append(f"{table}.csv 列集合与字典不一致")
        recomputed = data_digest(delivered)
        if recomputed != manifest["digests"]["data_digest"]:
            problems.append(
                "交付 CSV 重算的数据摘要与 manifest 不一致："
                f"{recomputed} != {manifest['digests']['data_digest']}"
            )

        evidence = decision.get("evidence", {})
        if decision.get("decision_version") != DECISION_VERSION:
            problems.append(
                f"决策版本 {decision.get('decision_version')} 与本校验器支持的 "
                f"{DECISION_VERSION} 不一致"
            )
        if evidence.get("validation_run") != run_id:
            problems.append(
                f"决策引用的验证批次 {evidence.get('validation_run')} != 实际 {run_id}"
            )
        if evidence.get("validation_manifest") != self.cfg.rel(manifest_path):
            problems.append("决策引用的 manifest 路径与本次校验对象不一致")
        if evidence.get("validation_data_digest") != manifest["digests"]["data_digest"]:
            problems.append("决策引用的数据摘要不是当前验证产物")
        if evidence.get("validation_generated_at_utc") != manifest["generated_at_utc"]:
            problems.append("决策引用的验证时刻与 manifest 不一致")
        if str(decision.get("decided_at_utc", "")) < str(
            manifest["generated_at_utc"]
        ):
            problems.append(
                "执行顺序不成立：decided_at_utc 早于验证产物 generated_at_utc"
            )

        for dotted, declared in evidence.get("metrics", {}).items():
            try:
                observed = _resolve(manifest["metrics"], dotted)
            except KeyError:
                problems.append(f"决策引用的指标不存在：{dotted}")
                continue
            if str(observed) != str(declared):
                problems.append(
                    f"决策引用的指标 {dotted}={declared} 与实际 {observed} 不一致"
                )

        first_blocks = manifest["scope_inputs"]["first_event_block_by_token"]
        later_token = max(first_blocks, key=lambda token: first_blocks[token])
        start = decision.get("observation_start_block")
        if start != first_blocks[later_token]:
            problems.append(
                f"观察期起点 {start} 不等于较晚发行代币 {later_token} 的首个事件区块 "
                f"{first_blocks[later_token]}"
            )
        cutoff = decision.get("study_cutoff_block")
        if cutoff != manifest["inputs"]["study_cutoff_block"]:
            problems.append(
                f"截止区块 {cutoff} 与验证产物使用的 "
                f"{manifest['inputs']['study_cutoff_block']} 不一致"
            )
        if cutoff != manifest["scope_inputs"]["snapshot_end_block"]:
            problems.append("截止区块不等于日志快照的 end_block，重放范围无法闭合")
        cutoff_hash = decision.get("study_cutoff_block_hash")
        if cutoff_hash != manifest["scope_inputs"].get("cutoff_block_hash"):
            problems.append("截止区块哈希与验证产物中的 canonical 区块头不一致")
        finalized = manifest["scope_inputs"].get("finalized_head_block", 0)
        if isinstance(cutoff, int) and int(finalized) < cutoff:
            problems.append(
                f"截止区块 {cutoff} 高于验证时的 finalized 头 {finalized}"
            )
        declared_finalized = decision.get("study_cutoff_finalized_head_block")
        if declared_finalized is not None:
            try:
                declared_finalized_block = int(declared_finalized)
            except (TypeError, ValueError):
                problems.append("study_cutoff_finalized_head_block 不是整数区块号")
            else:
                if declared_finalized_block != int(finalized):
                    problems.append(
                        f"决策声明的 finalized 头 {declared_finalized} "
                        f"与验证产物 {finalized} 不一致"
                    )

        if isinstance(start, int) and isinstance(cutoff, int):
            blocks = [
                int(row["block_number"]) for row in delivered["events"]
            ]
            if blocks and not (start <= min(blocks) and max(blocks) <= cutoff):
                problems.append("样本事件存在观察期之外的事件行")

        scope = decision.get("scope", {})
        if scope.get("chains") != [self.cfg.chain["name"]]:
            problems.append("样本范围的链集合与采集范围不一致")
        if scope.get("tokens") != list(self.cfg.token_order):
            problems.append("样本范围的代币集合与登记的代币不一致")

        snapshot = json.loads(self.cfg.log_snapshot.read_text(encoding="utf-8"))
        # 分母按登记的代币集合取全：某代币在起点前没有事件时应为 0，而不是缺键。
        before: dict[str, int] = {token: 0 for token in self.cfg.token_order}
        for log in snapshot["logs"]:
            token = contracts.contract_for_address(self.cfg.registry, log["address"])
            if token is None or not isinstance(start, int):
                continue
            if int(log["blockNumber"], 16) < start:
                before[token] = before.get(token, 0) + 1
        declared_before = scope.get("events_before_start_by_token", {})
        try:
            declared_before_counts = {key: int(value) for key, value in declared_before.items()}
        except (AttributeError, TypeError, ValueError):
            problems.append("events_before_start_by_token 应为代币到整数计数的映射")
        else:
            if declared_before_counts != before:
                problems.append(
                    "决策声明的观察期前事件数与快照实算不一致："
                    f"{declared_before} != {before}"
                )

        multi = scope.get("multi_event_transactions")
        observed_multi = manifest["scope_inputs"]["txs_with_multiple_events"]
        if multi == "structurally_absent" and observed_multi != 0:
            problems.append("决策称多事件交易结构性不可得，但快照中存在该类交易")
        if multi != "structurally_absent" and observed_multi == 0:
            problems.append("决策未声明多事件交易的结构性缺失，与快照不符")

        declared_slots = manifest["scope_inputs"]["declared_sample_slots"]
        if [record["slot"] for record in sample.get("records", [])] != declared_slots:
            problems.append("样本槽位清单与验证产物登记的不一致")

        supported = decision.get("supported_by", {})
        required_metrics = {
            "event_retrieval_coverage.coverage",
            "balance_reconstruction_error.max_abs_error_token",
            "unverifiable_share.share",
        }
        missing = required_metrics - set(evidence.get("metrics", {}))
        if missing:
            problems.append(f"决策未引用必需指标：{sorted(missing)}")
        for name, paths in supported.items():
            if not isinstance(paths, list) or not paths:
                problems.append(f"supported_by.{name} 应为非空证据路径列表")
                continue
            for dotted in paths:
                try:
                    _resolve(manifest, str(dotted))
                except KeyError:
                    problems.append(
                        f"supported_by.{name} 引用了验证产物中不存在的证据：{dotted}"
                    )
        return problems
