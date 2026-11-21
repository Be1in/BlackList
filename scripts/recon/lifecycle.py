"""Lifecycle case reconstruction: one declared case = an ordered set of transactions.

A case is a set of transactions of the same freeze lifecycle (freeze -> unfreeze ->
re-freeze, destruction, version switch). Every transaction goes through the same
pipeline as ticket 02 (``Reconstruction``); this module only merges the outputs and
adds case-level checks against independent references: an independent provider's
receipt, the ticket-01 manually checked sample, and the proxy upgrade boundary.

Nothing here re-implements parsing: the verification boundary stays at the
reconstruction output (``spec.md`` -> Testing Decisions).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from . import contracts
from .config import Config
from .pipeline import REVIEWER, Reconstruction
from .rpc import RpcCache, RpcError, now_utc, sha256_hex
from .tables import (
    assert_columns,
    artifact_digest,
    data_digest,
    load_dictionary,
    write_bundle,
)

CASES_PATH = "config/lifecycle_cases.json"
SAMPLE_RECORDS = "data/raw/sample_records.json"
SAMPLE_SOURCE = SAMPLE_RECORDS + "（工单 01 人工核对样本，独立端点取值）"

# 期望字段 -> (check_type, field_name)：field_name 必须是字典内字段名。
EXPECTATION_FIELDS: dict[str, tuple[str, str]] = {
    "event_classes": ("manual_reference", "event_class"),
    "blacklist_transitions": ("manual_reference", "blacklist_transition"),
    "contract_version_labels": ("manual_reference", "contract_version_label"),
    "token_symbols": ("manual_reference", "token_symbol"),
    "affected_addresses": ("manual_reference", "affected_address"),
    "destruction_amount_raw": ("manual_reference", "destruction_amount_raw"),
    # 期望比较的是 balances.balance_raw（该次 eth_call 原值）；D1 合计另在
    # events.balance_pre_event_raw，两者不在同一张表，不能混用字段名。
    "at_freeze_raw": ("balance_state", "balance_raw"),
    "at_event_raw": ("balance_state", "balance_raw"),
    "at_study_cutoff_raw": ("balance_state", "balance_raw"),
}


def load_cases(cfg: Config, relative: str = CASES_PATH) -> tuple[dict, str]:
    path = cfg.path(relative)
    return json.loads(path.read_text(encoding="utf-8")), cfg.rel(path)


def _truthy(value: object) -> bool:
    """样本里的布尔量可能是 bool、0/1、或 "true"/"false" 字符串。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "1", "yes"}:
            return True
        if text in {"false", "0", "no", ""}:
            return False
        raise ValueError(f"无法解释的布尔值: {value!r}")
    return bool(int(value))


def flatten(value: object) -> str:
    if isinstance(value, dict):
        return "|".join(f"{key}={value[key]}" for key in sorted(value))
    if isinstance(value, (list, tuple)):
        return "|".join(str(item) for item in value)
    return str(value)


def validation_row(
    check_type: str,
    field_name: str,
    contract_value: str,
    reference_value: str,
    reference_source: str,
    *,
    event_id: str = "",
    reference_at: str = "",
    deviation: str = "",
    reason: str = "",
    resolution: str = "",
    agreement: str | None = None,
) -> dict:
    agreed = agreement
    if agreed is None:
        agreed = "match" if contract_value == reference_value else "mismatch"
        if agreed == "mismatch" and not reason:
            reason = "unresolved"
    return {
        "validation_id": "",
        "event_id": event_id,
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


def _observed(expectation: str, events: list[dict], balances: list[dict]) -> object:
    if expectation == "event_classes":
        return [row["event_class"] for row in events]
    if expectation == "blacklist_transitions":
        return [row["blacklist_transition"] for row in events]
    if expectation == "contract_version_labels":
        return [row["contract_version_label"] for row in events]
    if expectation == "token_symbols":
        return [row["token_symbol"] for row in events]
    if expectation == "affected_addresses":
        return [row["affected_address"] for row in events]
    if expectation == "destruction_amount_raw":
        return [row["destruction_amount_raw"] for row in events]
    if expectation in {"at_freeze_raw", "at_event_raw"}:
        wanted = "at_freeze" if expectation == "at_freeze_raw" else "at_event"
        order = {row["event_id"]: row["log_order_key"] for row in events}
        rows = [row for row in balances if row["balance_reference"] == wanted]
        rows.sort(key=lambda row: order.get(row["reference_event_id"], ""))
        return [row["balance_raw"] for row in rows]
    if expectation == "at_study_cutoff_raw":
        observed: dict[str, str] = {}
        for row in balances:
            if row["balance_reference"] != "at_study_cutoff":
                continue
            if row["address"] in observed:
                raise SystemExit(
                    f"案例含同一地址的两个代币（{row['address']}），"
                    "at_study_cutoff_raw 预期无法只用地址唯一定位"
                )
            observed[row["address"]] = row["balance_raw"]
        return observed
    raise SystemExit(f"未知的 expect 字段: {expectation}")


def _expectation_rows(
    case: dict, events: list[dict], balances: list[dict], cases_path: str
) -> list[dict]:
    """Declared expectations (hand-derived) vs. the reconstructed rows."""
    rows: list[dict] = []
    for name, declared in case.get("expect", {}).items():
        if name == "sample_slots":
            continue
        if name not in EXPECTATION_FIELDS:
            raise SystemExit(f"{cases_path}: 未知的 expect 字段 {name}")
        check_type, field_name = EXPECTATION_FIELDS[name]
        observed = _observed(name, events, balances)
        rows.append(
            validation_row(
                check_type,
                field_name,
                flatten(observed),
                flatten(declared),
                f"{cases_path} 事先声明的预期（依据 docs/definitions.md 与工单 01 人工核对样本推导）",
                resolution="案例级预期；不一致即判失败，不允许事后修改预期",
            )
        )
    return rows


def merge_bundles(results: list[dict]) -> dict[str, list[dict]]:
    """Union of reconstruction outputs: 事件按 event_id、余额按 query_id 去重。

    核对行不做去重：每条行都写明自己的参照来源，重复合并会掩盖来源差异。
    """
    events: list[dict] = []
    seen_events: set[str] = set()
    for result in results:
        for event in result["events"]:
            if event["event_id"] in seen_events:
                continue
            seen_events.add(event["event_id"])
            events.append(event)
    events.sort(key=lambda event: event["log_order_key"])

    balances: list[dict] = []
    seen_queries: set[str] = set()
    for result in results:
        for row in result["balances"]:
            if row["query_id"] in seen_queries:
                continue
            seen_queries.add(row["query_id"])
            balances.append(row)

    validation: list[dict] = [
        row for result in results for row in result["validation"]
    ]
    return {"events": events, "balances": balances, "validation": validation}


@dataclass
class CaseRun:
    cfg: Config
    case_id: str
    case: dict
    cutoff_block: int
    cases_path: str = CASES_PATH
    dictionary: dict[str, list[str]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    deviations: list[dict] = field(default_factory=list)
    cache_stats: dict[str, int] = field(
        default_factory=lambda: {"state_calls_from_cache": 0, "probes_from_cache": 0}
    )

    def __post_init__(self) -> None:
        self.rpc = RpcCache(self.cfg)
        self.topics = contracts.verify_topic_table(self.cfg.registry)
        self.dictionary = load_dictionary(self.cfg.dictionary_csv)

    # ------------------------------------------------------------------ inputs
    def _reconstruct_targets(self) -> list[dict]:
        results: list[dict] = []
        for target in self.case["targets"]:
            reconstruction = Reconstruction(
                cfg=self.cfg,
                tx_hash=target["tx_hash"],
                cutoff_block=self.cutoff_block,
                log_index=None,
                expected_token=target.get("expected_token", ""),
            )
            result = reconstruction.run()
            for key, value in reconstruction.cache_stats.items():
                self.cache_stats[key] += value
            self.notes.extend(reconstruction.notes)
            self.deviations.extend(reconstruction.deviations)
            for event in result["events"]:
                expected = target.get("expected_event_class")
                if expected and event["event_class"] != expected:
                    raise SystemExit(
                        f"{target['tx_hash']}: 期望 {expected}，重建得到 {event['event_class']}"
                    )
            results.append(result)
        return results

    # ------------------------------------------------------- sequence & receipts
    def _sequence_rows(self, events: list[dict]) -> list[dict]:
        tokens = {
            self.cfg.tokens[symbol]["contract"].lower()
            for symbol in self.cfg.token_order
        }
        rows: list[dict] = []
        for target in self.case["targets"]:
            tx_hash = target["tx_hash"].lower()
            in_tx = [event for event in events if event["tx_hash"] == tx_hash]
            contract_value = "|".join(event["log_order_key"] for event in in_tx)
            event_id = in_tx[0]["event_id"] if len(in_tx) == 1 else ""
            try:
                receipt = self._probe(
                    "eth_getTransactionReceipt", [target["tx_hash"]], provider="reference"
                )
                result = receipt.result
                block_number = int(result["blockNumber"], 16)
                tx_index = int(result["transactionIndex"], 16)
                registered = [
                    log
                    for log in result["logs"]
                    if log["address"].lower() in tokens
                    and log["topics"]
                    and log["topics"][0].lower() in self.topics
                ]
                reference_value = "|".join(
                    f"{block_number:010d}:{tx_index:04d}:{int(log['logIndex'], 16):06d}"
                    for log in registered
                )
                rows.append(
                    validation_row(
                        "source_position",
                        "log_order_key",
                        contract_value,
                        reference_value,
                        f"{receipt.provider_label} eth_getTransactionReceipt 中按已登记合约与 topic 过滤的事件序列",
                        event_id=event_id,
                        reference_at=receipt.retrieved_at_utc,
                        resolution=(
                            f"交易共 {len(result['logs'])} 条日志，其中已登记事件 "
                            f"{len(registered)} 条；未登记日志不产生事件行，顺序按 log_order_key"
                        ),
                    )
                )
            except (RpcError, KeyError, TypeError, ValueError):
                rows.append(
                    validation_row(
                        "source_position",
                        "log_order_key",
                        contract_value,
                        "reference_unavailable",
                        "独立端点不可用",
                        event_id=event_id,
                        deviation="reference_missing",
                        reason="external_source_unavailable",
                        resolution="R3：计入 unverifiable share，不计为 match",
                        agreement="unverifiable",
                    )
                )
        return rows

    # ----------------------------------------------------- manually checked sample
    def _sample_rows(self, events: list[dict], balances: list[dict]) -> list[dict]:
        path = self.cfg.path(SAMPLE_RECORDS)
        if not path.exists():
            return []
        sample = json.loads(path.read_text(encoding="utf-8"))
        index = {
            (record["event"]["tx"].lower(), int(record["event"]["log_index"])): record
            for record in sample.get("records", [])
        }
        # 同一地址可能在两个代币合约上各有一条截止日余额，键必须带代币。
        cutoff_by_position = {
            (row["token_symbol"], row["address"]): row
            for row in balances
            if row["balance_reference"] == "at_study_cutoff"
        }
        rows: list[dict] = []
        for event in events:
            record = index.get((event["tx_hash"], int(event["log_index"])))
            if record is None:
                continue
            event_id = event["event_id"]
            for field_name, key in (
                ("blacklist_state_before", "before_blacklisted"),
                ("blacklist_state_after", "after_blacklisted"),
            ):
                if record.get(key) is None:
                    continue
                expected = "blacklisted" if _truthy(record[key]) else "not_blacklisted"
                rows.append(
                    validation_row(
                        "manual_reference",
                        field_name,
                        event[field_name],
                        expected,
                        SAMPLE_SOURCE,
                        event_id=event_id,
                        resolution=f"样本槽位 {record['slot']}",
                    )
                )
            if record.get("pre_balance_alchemy") is not None:
                # 样本自己带 adjustment（独立于本流程的调整量计算）：两个口径分开核对，
                # 避免用被测值构造期望值。
                sample_adjustment = record.get("adjustment")
                if sample_adjustment is not None:
                    rows.append(
                        validation_row(
                            "balance_state",
                            "balance_pre_event_adjustment_raw",
                            event["balance_pre_event_adjustment_raw"],
                            str(sample_adjustment),
                            SAMPLE_SOURCE,
                            event_id=event_id,
                            resolution=f"样本槽位 {record['slot']}：同区块更早 Transfer 调整量对照",
                        )
                    )
                expected_total = int(record["pre_balance_alchemy"]) + int(
                    sample_adjustment or 0
                )
                rows.append(
                    validation_row(
                        "balance_state",
                        "balance_pre_event_raw",
                        event["balance_pre_event_raw"],
                        str(expected_total),
                        SAMPLE_SOURCE,
                        event_id=event_id,
                        resolution=(
                            f"样本槽位 {record['slot']}：状态余额 "
                            f"{record['pre_balance_alchemy']} + 样本调整量 "
                            f"{sample_adjustment if sample_adjustment is not None else 'n/a'}"
                            "；只有 freezing 行属于 D1，其余为 at_event 诊断值"
                        ),
                    )
                )
            record_hash = record.get("code_hash_alchemy") or record.get("code_hash")
            if record_hash:
                rows.append(
                    validation_row(
                        "order_version",
                        "contract_code_hash",
                        event["contract_code_hash"],
                        record_hash.lower(),
                        SAMPLE_SOURCE,
                        event_id=event_id,
                        resolution=f"样本槽位 {record['slot']}：代码哈希对照",
                    )
                )
            cutoff = record.get("cutoff") or {}
            row = cutoff_by_position.get(
                (event["token_symbol"], event["affected_address"])
            )
            sample_block = cutoff.get("block")
            block_matches = sample_block is None or int(sample_block) == self.cutoff_block
            if cutoff.get("balance") is not None:
                if row is None:
                    rows.append(
                        validation_row(
                            "balance_state",
                            "balance_reference",
                            "no at_study_cutoff row",
                            "balance row expected at study cutoff",
                            SAMPLE_SOURCE,
                            event_id=event_id,
                            deviation="missing_cutoff_row",
                            reason="sample_record_has_cutoff_balance",
                            resolution="样本记录判定截止日仍冻结且有余额，重建未产生 D2 行",
                        )
                    )
                elif not block_matches:
                    rows.append(
                        validation_row(
                            "balance_state",
                            "balance_block_number",
                            row["balance_block_number"],
                            str(sample_block),
                            SAMPLE_SOURCE,
                            event_id=event_id,
                            deviation="cutoff_block_differs",
                            reason="cutoff_block_mismatch",
                            resolution="本次 study_cutoff_block 与样本记录不同，D2 不可直接对照",
                            agreement="unverifiable",
                        )
                    )
                else:
                    rows.append(
                        validation_row(
                            "balance_state",
                            "balance_raw",
                            row["balance_raw"],
                            str(cutoff["balance"]),
                            SAMPLE_SOURCE,
                            event_id=event_id,
                            resolution=(
                                f"样本槽位 {record['slot']}：D2 对照"
                                f"（样本与本次 study_cutoff_block 均为 {self.cutoff_block}）"
                            ),
                        )
                    )
            elif row is not None:
                rows.append(
                    validation_row(
                        "balance_state",
                        "balance_reference",
                        row["balance_reference"],
                        "no at_study_cutoff row",
                        SAMPLE_SOURCE,
                        event_id=event_id,
                        deviation="unexpected_cutoff_row",
                        reason="sample_record_has_no_cutoff_balance",
                        resolution="样本记录判定截止日不在黑名单集合，本应无 D2 行",
                        agreement="mismatch" if block_matches else "unverifiable",
                    )
                )
        return rows

    # ------------------------------------------------------- D1/D2 attribution
    def _attribution_rows(self, events: list[dict], balances: list[dict]) -> list[dict]:
        episode_starts = [
            event["event_id"]
            for event in events
            if event["event_class"] == "freezing"
            and event["blacklist_state_before"] == "not_blacklisted"
        ]
        at_freeze = [
            row["reference_event_id"]
            for row in balances
            if row["balance_reference"] == "at_freeze"
        ]
        at_event = [
            row["reference_event_id"]
            for row in balances
            if row["balance_reference"] == "at_event"
        ]
        diagnosed = [
            event["event_id"]
            for event in events
            if event["event_class"] in {"unfreezing", "destruction"}
        ]
        order = {event["event_id"]: event["log_order_key"] for event in events}
        for bucket in (at_freeze, at_event, diagnosed):
            bucket.sort(key=lambda event_id: order.get(event_id, ""))
        return [
            validation_row(
                "balance_state",
                "balance_reference",
                "|".join(at_freeze),
                "|".join(episode_starts),
                "events 表按 definitions.md 2.4.7 推导的 episode 起点",
                resolution="D1 只归属每个 episode 的第一条 freezing；重复日志（R9）不产生 D1",
            ),
            validation_row(
                "balance_state",
                "balance_reference",
                "|".join(at_event),
                "|".join(diagnosed),
                "events 表按 definitions.md 3.2 推导的 at_event 行",
                resolution="at_event 只用于 unfreezing / destruction，不得当作 D1 报告",
            ),
        ]

    # -------------------------------------------------------- structural finding
    def _structural_rows(self, events: list[dict]) -> tuple[list[dict], dict]:
        raw = self.cfg.log_snapshot.read_bytes()
        snapshot = json.loads(raw.decode("utf-8"))
        counts: dict[str, int] = {}
        for log in snapshot["logs"]:
            key = log["transactionHash"].lower()
            counts[key] = counts.get(key, 0) + 1
        multi = sum(1 for count in counts.values() if count > 1)
        targets = sorted({event["tx_hash"] for event in events})
        observed = [sum(1 for event in events if event["tx_hash"] == tx) for tx in targets]
        from_snapshot = [counts.get(tx, 0) for tx in targets]
        finding = {
            "snapshot_sha256": sha256_hex(raw),
            "log_count": len(snapshot["logs"]),
            "tx_count": len(counts),
            "txs_with_multiple_events": multi,
            "case_txs_with_multiple_events": sum(1 for count in observed if count > 1),
            "case_tx_event_counts": dict(zip(targets, observed)),
        }
        return (
            [
                validation_row(
                    "source_position",
                    "tx_event_count",
                    "|".join(str(count) for count in observed),
                    "|".join(str(count) for count in from_snapshot),
                    f"{self.cfg.rel(self.cfg.log_snapshot)} 按 tx_hash 分组重算（独立于重建代码）",
                    resolution=(
                        "每个目标交易一行应等于该交易在快照中的已登记日志数；"
                        f"快照共 {finding['log_count']} 条日志、{finding['tx_count']} 笔交易，"
                        f"其中含两条以上黑名单事件的交易数为 {multi}。"
                        "多事件交易在本范围结构性不可得，替代验证为交易作用域下的多日志交易"
                        "（本案例）与同一地址的跨交易事件序列（unfreeze/destruction 案例）"
                    ),
                )
            ],
            finding,
        )

    # --------------------------------------------------------- version boundary
    def _probe(self, method: str, params: list, provider: str = "primary"):
        probe = self.rpc.probe(method, params, provider=provider)
        self.cache_stats["probes_from_cache"] += int(probe.from_cache)
        return probe

    def _impl_address(self, proxy: str, slot: str, block: int) -> str:
        probe = self._probe(
            "eth_getStorageAt", [proxy, slot, hex(block)], provider="primary"
        )
        address = contracts.word_to_address(contracts.word_at(probe.result, 0))
        if int(address, 16) == 0:
            raise RpcError(f"{proxy} 的代理槽 {slot} 在区块 {block} 为零地址")
        return address

    def _code_hash(
        self, address: str, block: int, provider: str = "primary"
    ) -> tuple[str, str]:
        """(keccak256(code), 抓取时刻)；空代码不当作版本哈希。"""
        probe = self._probe("eth_getCode", [address, hex(block)], provider=provider)
        code = probe.result or ""
        if code in {"", "0x"}:
            raise RpcError(f"{address} 在区块 {block} 无代码")
        return (
            contracts.keccak_hex(bytes.fromhex(code.removeprefix("0x"))),
            probe.retrieved_at_utc,
        )

    @staticmethod
    def _first_true(predicate, low: int, high: int) -> int:
        """Smallest block in (low, high] satisfying ``predicate`` (monotone false->true)."""
        while low + 1 < high:
            middle = (low + high) // 2
            if predicate(middle):
                high = middle
            else:
                low = middle
        return high

    @staticmethod
    def _boundary_unverifiable(label: str, detail: str) -> dict:
        return validation_row(
            "order_version",
            "contract_code_hash",
            f"{label}_switch_undetermined",
            "no reliable switch block",
            "代理实现槽二分 + 两侧取值复核",
            deviation="switch_block_unverified",
            reason="non_monotonic_or_missing_code",
            resolution=f"{label} 切换点无法确认：{detail}",
            agreement="unverifiable",
        )

    def _version_boundary_rows(
        self, events: list[dict], boundary: dict
    ) -> tuple[list[dict], list[dict]]:
        token = boundary.get("token", "")
        slot = self.cfg.registry["proxy_slots"][boundary["proxy_slot"]]
        proxy = self.cfg.tokens[token]["contract"].lower()
        blocks = sorted(int(event["block_number"]) for event in events)
        if len(blocks) < 2 or blocks[0] == blocks[-1]:
            return [], []
        low, high = blocks[0], blocks[-1]
        base = self._impl_address(proxy, slot, low)
        tip = self._impl_address(proxy, slot, high)
        if base == tip:
            return (
                [
                    validation_row(
                        "order_version",
                        "contract_code_hash",
                        f"{base}|{tip}",
                        f"{base}|{tip}",
                        "代理实现槽在两次事件区块的读取（归档端点）",
                        deviation="no_version_change",
                        reason="proxy_not_upgraded_between_events",
                        resolution="两次事件之间代理实现未变化，版本切换不成立",
                        agreement="unverifiable",
                    )
                ],
                [],
            )

        candidates = (
            (
                "first",
                self._first_true(
                    lambda block: self._impl_address(proxy, slot, block) != base,
                    low,
                    high,
                ),
            ),
            (
                "last",
                self._first_true(
                    lambda block: self._impl_address(proxy, slot, block) == tip,
                    low,
                    high,
                ),
            ),
        )

        rows: list[dict] = []
        findings: list[dict] = []
        for label, switch_block in candidates:
            # 二分只在「切换后不回退」的单调区间上成立：结果必须用两侧取值复核，
            # 否则如实记为 unverifiable，不把错误区块当成切换点。
            try:
                impl_before = self._impl_address(proxy, slot, switch_block - 1)
                impl_after = self._impl_address(proxy, slot, switch_block)
            except RpcError as exc:
                rows.append(self._boundary_unverifiable(label, str(exc)))
                continue
            verified = (
                impl_before == base and impl_after != base
                if label == "first"
                else impl_after == tip and impl_before != tip
            )
            if not verified:
                rows.append(
                    self._boundary_unverifiable(
                        label, "代理实现在区间内非单调变化，二分结果不能作为切换点"
                    )
                )
                continue

            try:
                hash_before, at_before = self._code_hash(impl_before, switch_block - 1)
                hash_after, at_after = self._code_hash(impl_after, switch_block)
            except RpcError as exc:
                rows.append(self._boundary_unverifiable(label, str(exc)))
                continue
            before_block = switch_block - 1
            label_before = contracts.version_for_code_hash(
                self.cfg.registry, token, hash_before
            )
            label_after = contracts.version_for_code_hash(
                self.cfg.registry, token, hash_after
            )
            try:
                reference_before, ref_at_before = self._code_hash(
                    impl_before, before_block, "reference"
                )
                reference_after, ref_at_after = self._code_hash(
                    impl_after, switch_block, "reference"
                )
                reference_source = "独立端点 eth_getCode（切换前后各一次）"
                reference_at = max(ref_at_before, ref_at_after)
                agreement = None
            except RpcError:
                reference_before = reference_after = "reference_unavailable"
                reference_source = "独立端点不可用"
                reference_at = max(at_before, at_after)
                agreement = "unverifiable"

            notes = (
                f"{label} switch at block {switch_block}: {impl_before} -> {impl_after}"
            )
            rows.append(
                validation_row(
                    "order_version",
                    "contract_code_hash",
                    f"{hash_before}|{hash_after}",
                    f"{reference_before}|{reference_after}",
                    reference_source,
                    reference_at=reference_at,
                    reason=(
                        "R4：指纹表未收录该实现哈希，版本标签留空，代码哈希仍绑定"
                        if not label_before.label or not label_after.label
                        else ""
                    ),
                    resolution=(
                        f"{notes}；版本标签 "
                        f"{label_before.label or 'unregistered'} -> "
                        f"{label_after.label or 'unregistered'}；"
                        "事件行各自记录 contract_version_label 与代码哈希"
                    ),
                    agreement=agreement,
                )
            )
            findings.append(
                {
                    "kind": label,
                    "block": switch_block,
                    "implementation_before": impl_before,
                    "implementation_after": impl_after,
                    "code_hash_before": hash_before,
                    "code_hash_after": hash_after,
                    "version_label_before": label_before.label,
                    "version_label_after": label_after.label,
                }
            )
        return rows, findings

    # -------------------------------------------------------------------- main
    def run(self) -> dict:
        results = self._reconstruct_targets()

        merged = merge_bundles(results)
        events = merged["events"]
        balances = merged["balances"]
        validation = list(merged["validation"])
        validation.extend(self._sequence_rows(events))
        validation.extend(self._sample_rows(events, balances))
        validation.extend(self._attribution_rows(events, balances))
        expectation_rows = _expectation_rows(
            self.case, events, balances, self.cases_path
        )
        validation.extend(expectation_rows)
        structural: dict = {}
        if self.case.get("structural_scan"):
            rows, structural = self._structural_rows(events)
            validation.extend(rows)

        boundaries: list[dict] = []
        boundary_def = self.case.get("version_boundary")
        if boundary_def:
            rows, boundaries = self._version_boundary_rows(events, boundary_def)
            validation.extend(rows)

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

        bundle = {
            "events": events,
            "balances": balances,
            "validation": validation,
        }
        return {
            "bundle": bundle,
            "structural_findings": structural,
            "version_boundaries": boundaries,
            "expectations": expectation_rows,
        }

    # ---------------------------------------------------------------- delivery
    def deliver(self, outcome: dict) -> dict:
        bundle = outcome["bundle"]
        for table, rows in bundle.items():
            assert_columns(table, self.dictionary[table], rows)

        case_key = f"lc_{self.case_id}"
        out_dir = self.cfg.out_root / case_key
        written, parquet_written = write_bundle(out_dir, self.dictionary, bundle)

        validation = bundle["validation"]
        counts = {
            "events": len(bundle["events"]),
            "balances": len(bundle["balances"]),
            "validation": len(validation),
            "validation_agreement": {
                value: sum(1 for row in validation if row["agreement"] == value)
                for value in sorted({row["agreement"] for row in validation})
            },
            "event_class": {
                value: sum(1 for row in bundle["events"] if row["event_class"] == value)
                for value in sorted({row["event_class"] for row in bundle["events"]})
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
        }
        expectations = [
            {
                "field": row["field_name"],
                "declared": row["reference_value"],
                "observed": row["contract_value"],
                "agreement": row["agreement"],
            }
            for row in outcome["expectations"]
        ]
        manifest = {
            "case_id": case_key,
            "title": self.case.get("title", ""),
            "summary": self.case.get("summary", ""),
            "dataset_contract_version": self.cfg.registry["dataset_contract_version"],
            "generated_at_utc": now_utc(),
            "inputs": {
                "log_snapshot": self.cfg.rel(self.cfg.log_snapshot),
                "log_snapshot_sha256": bundle["events"][0]["raw_log_sha256"],
                "study_cutoff_block": self.cutoff_block,
                "contract_registry": self.cfg.rel(self.cfg.contract_registry),
                "dictionary": self.cfg.rel(self.cfg.dictionary_csv),
                "cases": self.cases_path,
                "targets": self.case["targets"],
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
                "declared_for_this_case": sorted(
                    {row["balance_reference"] for row in bundle["balances"]}
                ),
            },
            "counts": counts,
            "expectations": expectations,
            "expectations_failed": [
                item for item in expectations if item["agreement"] != "match"
            ],
            "structural_findings": outcome["structural_findings"],
            "version_boundaries": outcome["version_boundaries"],
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
                f"python scripts/reconstruct_lifecycle.py --case {self.case_id} "
                f"--cutoff-block {self.cutoff_block} --cases {self.cases_path} "
                f"--log-snapshot {self.cfg.rel(self.cfg.log_snapshot)} "
                f"--contract-registry {self.cfg.rel(self.cfg.contract_registry)} "
                f"--dictionary {self.cfg.rel(self.cfg.dictionary_csv)} "
                f"--out-root {self.cfg.rel(self.cfg.out_root)}"
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
