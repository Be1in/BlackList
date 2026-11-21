"""Restricted-subset analyses: timing gaps, outflows, frozen balances (ticket 08).

The core path (tickets 05/07) answers "what happened" over every retrievable event.
This module answers a narrower question on a *separate* path: on the subset whose
evidence is sufficient, what do public sanctions timing gaps, address outflows and
frozen balances look like? It reads the ticket-05 core bundle and the ticket-06 match
bundle, writes its own bundle, and never adds anything to the core comparison.

Two subset definitions, each re-checkable step by step in ``subset_eligibility.csv``:

``timing_gap``
    episode start (state really became blacklisted) + ``ofac_sdn`` match_status =
    ``matched`` + a dated sanctions record (``sanctions_public_timestamp`` with its
    ``sanctions_date_basis``). A snapshot publish date is *not* a designation date,
    so a matched address without a dated sanctions record stays out (R6).
``exchange_outflow``
    episode start + ``exchange_label`` match_status = ``matched`` + ``label_date``
    not later than the event date (R5, look-ahead protection).

Statistics, all descriptive and restricted to those subsets:

* ``timing_gaps.csv``: ``date(block_time_utc) − sanctions_public_timestamp`` in whole
  days (median / P90 / min / max / same-day share). Resolution is one day by
  construction: two timestamps on the same day cannot be ordered.
* ``exchange_outflows.csv``: outflows leaving the frozen address inside
  ``[freeze_time − window, freeze_time)``, as an event share and a token total. This
  needs a pinned transfer-log snapshot on disk; with no snapshot every outflow metric
  is ``not_reportable`` (``transfer_logs_not_collected``), never 0. An event whose
  window is not covered by the snapshot is excluded and counted, not read as zero.
* ``frozen_balances.csv``: restricted-subset balances use **D1 only**
  (``at_freeze``), declared in every row. D2 is address-level at the study cutoff and
  is not reported on these subsets.
* ``outflow_window_sensitivity.csv``: the outflow window is a parameter; every
  setting is reported next to the baseline, and ``conclusion_affected`` says
  ``not_assessable`` when there is no measurement to compare.

Separation is structural rather than a promise: a separate batch directory, distinct
table names, every row carries ``coverage_scope = restricted_subset`` and
``statement_type = descriptive``, and no issuer difference is produced here (that is
ticket 07's job under its own preconditions). ``--check`` re-asserts all of it.
"""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from .comparison import is_episode_start
from .config import Config
from .core import locate_core_bundle
from .matching import MatchingRun
from .rpc import canonical_json, now_utc, sha256_hex
from .tables import (
    PROVENANCE_TIME_FIELDS,
    artifact_digest,
    assert_columns,
    data_digest,
    load_dictionary,
    read_table,
    share,
    write_csv,
    write_parquet,
)

TRANSFERS_PATH = "data/raw/transfers/ethereum_token_transfers.json"

SUBSET_TABLE = "subset_eligibility"
TIMING_TABLE = "timing_gaps"
OUTFLOW_TABLE = "exchange_outflows"
BALANCE_TABLE = "frozen_balances"
SENSITIVITY_TABLE = "outflow_window_sensitivity"
TABLES = (SUBSET_TABLE, TIMING_TABLE, OUTFLOW_TABLE, BALANCE_TABLE, SENSITIVITY_TABLE)

SUBSET_FIELDS = [
    "analysis",
    "issuer",
    "step_index",
    "step_id",
    "rule",
    "events_in",
    "events_dropped",
    "events_remaining",
    "dropped_reason_codes",
    "statement_type",
    "note",
]
TIMING_FIELDS = [
    "issuer",
    "metric_id",
    "metric_label",
    "value",
    "unit",
    "numerator",
    "denominator",
    "denominator_definition",
    "coverage_scope",
    "status",
    "statement_type",
    "gap_resolution_days",
    "timestamp_precision",
    "note",
]
OUTFLOW_FIELDS = [
    "issuer",
    "outflow_window",
    "window_hours",
    "metric_id",
    "metric_label",
    "value",
    "unit",
    "numerator",
    "denominator",
    "denominator_definition",
    "window_covered_events",
    "window_not_covered_events",
    "coverage_scope",
    "status",
    "statement_type",
    "note",
]
BALANCE_FIELDS = [
    "issuer",
    "subset_id",
    "metric_id",
    "metric_label",
    "value",
    "unit",
    "numerator",
    "denominator",
    "denominator_definition",
    "coverage_scope",
    "balance_reference",
    "status",
    "statement_type",
    "note",
]
SENSITIVITY_FIELDS = [
    "parameter",
    "setting",
    "issuer",
    "metric_id",
    "numerator",
    "denominator",
    "value",
    "status",
    "delta_vs_baseline",
    "conclusion_affected",
    "window_covered_events",
    "window_not_covered_events",
    "statement_type",
    "note",
]
FIELD_ORDER = {
    SUBSET_TABLE: SUBSET_FIELDS,
    TIMING_TABLE: TIMING_FIELDS,
    OUTFLOW_TABLE: OUTFLOW_FIELDS,
    BALANCE_TABLE: BALANCE_FIELDS,
    SENSITIVITY_TABLE: SENSITIVITY_FIELDS,
}

ANALYSIS_TIMING = "timing_gap"
ANALYSIS_OUTFLOW = "exchange_outflow"
ANALYSES = (ANALYSIS_TIMING, ANALYSIS_OUTFLOW)

WINDOW_HOURS = (1, 6, 24, 72, 168)
WINDOW_SETTINGS = tuple(f"{hours}h" for hours in WINDOW_HOURS)
BASELINE = {"outflow_window": "24h"}

OFAC_LAYER = "ofac_sdn"
LABEL_LAYER = "exchange_label"
COVERAGE_SCOPE = "restricted_subset"
STATEMENT_TYPE = "descriptive"
STATUSES = {"reported", "not_reportable"}
# 公开指定日到冻结日的口径：负值（先冻结后指定）与 >365 天（实体早年指定、
# 地址后来才上榜）都不属于响应延迟，单列计数，不进入中位数/P90。
LATENCY_MAX_DAYS = 365
# 与工单 07 的派生视图同名会让"拼接"变成不小心就能做到的事，所以名字必须错开。
CORE_COMPARISON_VIEWS = ("comparison", "differences", "sensitivity")

# 余额口径：受限子集只用 D1；D2 是地址级截止日口径，不在这两个子集里报告。
BALANCE_REFERENCE = "at_freeze"
BALANCE_DEFINITION = (
    "D1 = 冻结日志生效前一刻的余额（events.balance_pre_event_raw，对应 "
    "balances.balance_reference = at_freeze）；子集不报告 D2"
)

TIMING_STEPS = (
    ("core_eligible", "analysis_eligibility == 'core'", "not_core_eligible"),
    (
        "episode_start",
        "event_class == 'freezing' 且 blacklist_transition = true 且 "
        "blacklist_state_before = not_blacklisted",
        "not_episode_start",
    ),
    (
        "ofac_matched",
        "matches(match_layer = ofac_sdn).match_status == 'matched'",
        "sanctions_record_missing",
    ),
    (
        "dated_sanctions",
        "sanctions_public_timestamp 非空且 sanctions_date_basis 非空",
        "label_date_missing",
    ),
)
OUTFLOW_STEPS = (
    ("core_eligible", "analysis_eligibility == 'core'", "not_core_eligible"),
    (
        "episode_start",
        "event_class == 'freezing' 且 blacklist_transition = true 且 "
        "blacklist_state_before = not_blacklisted",
        "not_episode_start",
    ),
    (
        "label_matched",
        "matches(match_layer = exchange_label).match_status == 'matched'",
        "external_source_unavailable",
    ),
    (
        "dated_label",
        "label_date 非空且 label_date 不晚于事件日期",
        "label_date_missing",
    ),
)
STEP_RULES = {ANALYSIS_TIMING: TIMING_STEPS, ANALYSIS_OUTFLOW: OUTFLOW_STEPS}

# ``--check`` 拒绝通过的理由清单：每条陈述消失都必须可见。
REQUIRED_CAVEATS = (
    "descriptive_only",
    "no_private_request_inference",
    "no_evasion_inference",
    "no_enforcement_effectiveness_inference",
    "not_extrapolated",
    "subset_rules_recheckable",
    "no_snapshot_date_as_sanctions_date",
    "missing_labels_not_zero",
    "transfer_absence_not_zero",
    "balances_one_definition",
    "separation_from_core",
    "window_dependence_disclosed",
)


def parse_utc(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def parse_date(text: str) -> date:
    return datetime.strptime(text, "%Y-%m-%d").date()


def _median(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal(2)


def _percentile(values: list[Decimal], fraction: float) -> Decimal | None:
    """Nearest-rank percentile: the smallest value at or above ``fraction``."""
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * fraction) - 1)
    return ordered[index]


def _fmt(value: Decimal) -> str:
    return format(value, ".6f")


def _token_amount(raw: str, decimals: int) -> Decimal:
    return Decimal(raw) / (Decimal(10) ** decimals)


def _assert_fields(table: str, fields: list[str], rows: list[dict]) -> None:
    for row in rows:
        if set(row) != set(fields):
            raise ValueError(
                f"{table}: 列集合与声明不一致 "
                f"extra={sorted(set(row) - set(fields))} "
                f"missing={sorted(set(fields) - set(row))}"
            )
        non_string = [key for key, value in row.items() if not isinstance(value, str)]
        if non_string:
            raise ValueError(f"{table}: 非字符串值 {sorted(non_string)}")


@dataclass
class TransferSnapshot:
    """A pinned transfer-log snapshot: address -> transfers, plus its block window.

    The block-time window is mandatory. Without it a window that starts before the
    snapshot cannot be distinguished from "no transfers happened", so the run stops
    instead of reporting an outflow of zero.
    """

    path: str
    sha256: str
    available: bool
    label: str = ""
    start_block: int | None = None
    end_block: int | None = None
    start_time_utc: str = ""
    end_time_utc: str = ""
    transfers_by_address: dict[str, list[dict]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def transfers(self) -> int:
        return sum(len(rows) for rows in self.transfers_by_address.values())

    def rows_for(self, address: str) -> list[dict]:
        return self.transfers_by_address.get(str(address).lower(), [])

    def covers(self, window_start: datetime, event_time: datetime) -> bool:
        """Whether the snapshot spans the whole window ``[window_start, event_time]``."""
        if not self.available or not self.start_time_utc or not self.end_time_utc:
            return False
        return (
            parse_utc(self.start_time_utc) <= window_start
            and event_time <= parse_utc(self.end_time_utc)
        )


def load_transfers(cfg: Config, relative: str) -> TransferSnapshot:
    """Parse the transfer-log snapshot; a malformed entry stops the run."""
    path = cfg.path(relative)
    if not path.exists():
        return TransferSnapshot(
            path=relative,
            sha256="",
            available=False,
            notes=[
                f"转账日志快照不存在：{relative}；"
                "exchange-outflow 一律按不可报告处理，不写 0"
            ],
        )

    raw = path.read_bytes()
    document = json.loads(raw.decode("utf-8"))
    start_time = str(document.get("start_block_time_utc", "")).strip()
    end_time = str(document.get("end_block_time_utc", "")).strip()
    if not start_time or not end_time:
        raise SystemExit(
            f"{relative}: 缺少 start_block_time_utc / end_block_time_utc；"
            "没有覆盖区间就无法区分「窗口内没流出」和「没查」"
        )
    parse_utc(start_time)
    parse_utc(end_time)

    grouped: dict[str, list[dict]] = defaultdict(list)
    for entry in document.get("transfers", []):
        address = str(entry.get("address", "")).strip().lower()
        if not (address.startswith("0x") and len(address) == 42):
            raise SystemExit(f"{relative}: 转账地址不可用：{entry.get('address')!r}")
        direction = str(entry.get("direction", "")).strip()
        if direction not in {"out", "in"}:
            raise SystemExit(
                f"{relative}: {address} 的 direction={direction!r} 不在 out/in 内"
            )
        amount = str(entry.get("amount_raw", "")).strip()
        if not amount.isdigit():
            raise SystemExit(f"{relative}: {address} 的 amount_raw 不是十进制整数")
        block_time = str(entry.get("block_time_utc", "")).strip()
        parse_utc(block_time)
        grouped[address].append(
            {
                "address": address,
                "direction": direction,
                "amount_raw": amount,
                "block_number": str(entry.get("block_number", "")).strip(),
                "block_time_utc": block_time,
                "tx_hash": str(entry.get("tx_hash", "")).strip(),
                "log_index": str(entry.get("log_index", "")).strip(),
            }
        )
    for rows in grouped.values():
        rows.sort(
            key=lambda row: (row["block_time_utc"], int(row["log_index"] or 0))
        )
    return TransferSnapshot(
        path=relative,
        sha256=sha256_hex(raw),
        available=True,
        label=str(document.get("label", "")).strip(),
        start_block=(
            int(document["start_block"]) if document.get("start_block") else None
        ),
        end_block=int(document["end_block"]) if document.get("end_block") else None,
        start_time_utc=start_time,
        end_time_utc=end_time,
        transfers_by_address=dict(grouped),
    )


def outflow_transfers(
    rows: list[dict], window_start: datetime, event_time: datetime
) -> list[dict]:
    """Outflow rows inside ``[window_start, event_time)``; incoming rows never count."""
    return [
        row
        for row in rows
        if row["direction"] == "out"
        and window_start <= parse_utc(row["block_time_utc"]) < event_time
    ]


@dataclass
class RestrictedRun:
    """Read the core + match bundles and write the restricted-subset bundle."""

    cfg: Config
    core_id: str = ""
    outflow_window: str = BASELINE["outflow_window"]
    transfers_path: str = TRANSFERS_PATH
    dictionary: dict[str, list[str]] = field(default_factory=dict)
    transfers: TransferSnapshot | None = None

    def __post_init__(self) -> None:
        if self.outflow_window not in WINDOW_SETTINGS:
            raise SystemExit(
                f"outflow_window={self.outflow_window!r} 不在 {WINDOW_SETTINGS} 内"
            )
        self.dictionary = load_dictionary(self.cfg.dictionary_csv)
        if self.transfers is None:
            self.transfers = load_transfers(self.cfg, self.transfers_path)

    # ----------------------------------------------------------------- inputs
    def match_inputs(self, core_manifest: dict) -> tuple[Path, dict, list[dict]]:
        """The ticket-06 bundle for this core bundle, located by its derived id."""
        match_run = MatchingRun(cfg=self.cfg, core_id=core_manifest["core_id"])
        match_id = match_run.run_id(core_manifest)
        match_dir = self.cfg.out_root / match_id
        manifest_path = match_dir / "manifest.json"
        if not manifest_path.exists():
            raise SystemExit(
                f"匹配层产物缺失：{self.cfg.rel(manifest_path)}；"
                "先运行 python scripts/reconstruct_matches.py"
            )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("match_batch_id") != match_id:
            raise SystemExit(
                f"{self.cfg.rel(manifest_path)} 的 match_batch_id="
                f"{manifest.get('match_batch_id')} 与输入派生的 {match_id} 不一致"
            )
        matches = read_table(match_dir / "matches.csv")
        assert_columns("matches", self.dictionary["matches"], matches)
        return match_dir, manifest, matches

    def load_inputs(
        self,
    ) -> tuple[Path, dict, list[dict], Path, dict, list[dict]]:
        core_dir, core_manifest = locate_core_bundle(
            self.cfg, self.core_id, require_current_inputs=True
        )
        events = read_table(core_dir / "events.csv")
        assert_columns("events", self.dictionary["events"], events)
        match_dir, match_manifest, matches = self.match_inputs(core_manifest)
        return core_dir, core_manifest, events, match_dir, match_manifest, matches

    # ----------------------------------------------------------------- subset
    def _match_index(
        self, matches: list[dict]
    ) -> tuple[dict[str, dict], dict[str, dict]]:
        ofac: dict[str, dict] = {}
        label: dict[str, dict] = {}
        for row in matches:
            if row["match_layer"] == OFAC_LAYER:
                ofac[row["event_id"]] = row
            elif row["match_layer"] == LABEL_LAYER:
                label[row["event_id"]] = row
        return ofac, label

    def _passes(
        self, step_id: str, event: dict, ofac: dict, label: dict
    ) -> bool:
        if step_id == "core_eligible":
            return event["analysis_eligibility"] == "core"
        if step_id == "episode_start":
            return is_episode_start(event)
        if step_id == "ofac_matched":
            row = ofac.get(event["event_id"])
            return row is not None and row["match_status"] == "matched"
        if step_id == "dated_sanctions":
            row = ofac.get(event["event_id"], {})
            return bool(
                row.get("match_status") == "matched"
                and row.get("sanctions_public_timestamp")
                and row.get("sanctions_date_basis")
            )
        if step_id == "label_matched":
            row = label.get(event["event_id"])
            return row is not None and row["match_status"] == "matched"
        if step_id == "dated_label":
            row = label.get(event["event_id"], {})
            label_date = row.get("label_date", "")
            return bool(
                row.get("match_status") == "matched"
                and label_date
                and label_date <= str(event.get("block_time_utc", ""))[:10]
            )
        raise ValueError(f"未知的子集步骤：{step_id}")

    def _drop_reason(
        self, step_id: str, event: dict, ofac: dict, label: dict, fallback: str
    ) -> str:
        if step_id == "ofac_matched":
            return ofac.get(event["event_id"], {}).get("missing_reason_code") or fallback
        if step_id == "label_matched":
            return label.get(event["event_id"], {}).get("missing_reason_code") or fallback
        if step_id == "dated_sanctions":
            row = ofac.get(event["event_id"], {})
            if row.get("sanctions_public_timestamp") and not row.get(
                "sanctions_date_basis"
            ):
                return "label_date_missing"
            return fallback
        if step_id == "dated_label":
            row = label.get(event["event_id"], {})
            label_date = row.get("label_date", "")
            event_day = str(event.get("block_time_utc", ""))[:10]
            if label_date and label_date > event_day:
                return "not_applicable"
            return fallback
        return fallback

    def funnel(self, events: list[dict], matches: list[dict]) -> dict:
        """Per analysis per issuer: the filter chain, the survivors and their rows."""
        ofac, label = self._match_index(matches)
        output: dict[str, dict[str, dict]] = {}
        for analysis in ANALYSES:
            steps = STEP_RULES[analysis]
            output[analysis] = {}
            for token in self.cfg.token_order:
                survivors = [
                    {"event": event, "match": None}
                    for event in events
                    if event["token_symbol"] == token
                ]
                rows: list[dict] = []
                for index, (step_id, rule, fallback) in enumerate(steps):
                    events_in = len(survivors)
                    kept: list[dict] = []
                    dropped: Counter = Counter()
                    for item in survivors:
                        event = item["event"]
                        if self._passes(step_id, event, ofac, label):
                            # 只在匹配步骤上挂上证据行；事件级步骤沿用上一层的结果。
                            if step_id in {"ofac_matched", "dated_sanctions"}:
                                match_row = ofac.get(event["event_id"])
                            elif step_id in {"label_matched", "dated_label"}:
                                match_row = label.get(event["event_id"])
                            else:
                                match_row = item["match"]
                            kept.append({"event": event, "match": match_row})
                        else:
                            dropped[
                                self._drop_reason(step_id, event, ofac, label, fallback)
                            ] += 1
                    survivors = kept
                    rows.append(
                        {
                            "analysis": analysis,
                            "issuer": token,
                            "step_index": str(index),
                            "step_id": step_id,
                            "rule": rule,
                            "events_in": str(events_in),
                            "events_dropped": str(sum(dropped.values())),
                            "events_remaining": str(len(survivors)),
                            "dropped_reason_codes": ";".join(sorted(dropped)),
                            "statement_type": STATEMENT_TYPE,
                            "note": (
                                "筛选链逐级可复核：events_in − events_dropped = "
                                "events_remaining；最后一级的 remaining 即该分析的子集规模"
                            ),
                        }
                    )
                output[analysis][token] = {"steps": rows, "items": survivors}
        return output

    def subset_size(self, funnel: dict, analysis: str, token: str) -> int:
        return len(funnel[analysis][token]["items"])

    def subset_rows(self, funnel: dict) -> list[dict]:
        rows: list[dict] = []
        for analysis in ANALYSES:
            for token in self.cfg.token_order:
                rows.extend(funnel[analysis][token]["steps"])
        return rows

    # --------------------------------------------------------------- timing gap
    def _timing_row(
        self,
        issuer: str,
        metric_id: str,
        label: str,
        *,
        value: str = "",
        unit: str = "days",
        numerator: str = "",
        denominator: str = "",
        status: str = "reported",
        note: str = "",
    ) -> dict:
        return {
            "issuer": issuer,
            "metric_id": metric_id,
            "metric_label": label,
            "value": value,
            "unit": unit,
            "numerator": numerator,
            "denominator": denominator,
            "denominator_definition": (
                "timing-gap 子集事件数（episode 起点 × OFAC matched × 有公开制裁日期）"
            ),
            "coverage_scope": COVERAGE_SCOPE,
            "status": status,
            "statement_type": STATEMENT_TYPE,
            "gap_resolution_days": "1",
            "timestamp_precision": "day",
            "note": note,
        }

    def timing_metrics(self, funnel: dict) -> list[dict]:
        rows: list[dict] = []
        for token in self.cfg.token_order:
            items = funnel[ANALYSIS_TIMING][token]["items"]
            gaps: list[Decimal] = []
            for item in items:
                event = item["event"]
                stamp = item["match"]["sanctions_public_timestamp"]
                delta = (
                    parse_utc(event["block_time_utc"]).date() - parse_date(stamp)
                ).days
                gaps.append(Decimal(delta))
            # 两个必须单列的桶：先冻结后指定、以及实体早年指定后才上榜的地址。
            before = [gap for gap in gaps if gap < 0]
            later = [gap for gap in gaps if gap > LATENCY_MAX_DAYS]
            latency = [gap for gap in gaps if 0 <= gap <= LATENCY_MAX_DAYS]
            same_day = [gap for gap in latency if gap == 0]
            measured = bool(gaps)
            rows.append(
                self._timing_row(
                    token,
                    "timing_gap_events",
                    "timing-gap 子集事件数",
                    value=str(len(gaps)) if measured else "",
                    unit="events",
                    numerator=str(len(gaps)),
                    status="reported" if measured else "not_reportable",
                    note=(
                        "分子即子集规模；事件为 episode 起点 × OFAC 匹配 × 有实体级指定日"
                        if measured
                        else "子集为空：没有 episode 起点同时具备 OFAC 匹配证据与公开指定日"
                    ),
                )
            )
            for metric_id, label, bucket, bucket_note in (
                (
                    "timing_gap_freeze_before_designation_events",
                    "冻结早于公开指定的子集事件数（单列）",
                    before,
                    "gap < 0：发行人在公开指定前已冻结；单列，不进入中位数，"
                    "不读作负延迟",
                ),
                (
                    "timing_gap_address_added_later_events",
                    "指定超过 365 天的子集事件数（单列）",
                    later,
                    "gap > 365 天：实体早年指定、地址后来才上榜；单列，"
                    "不读作响应延迟",
                ),
            ):
                rows.append(
                    self._timing_row(
                        token,
                        metric_id,
                        label,
                        value=str(len(bucket)) if measured else "",
                        unit="events",
                        numerator=str(len(bucket)),
                        denominator=str(len(gaps)),
                        status="reported" if measured else "not_reportable",
                        note=(
                            bucket_note
                            if measured
                            else "子集为空，计数不可报告（不是 0）"
                        ),
                    )
                )
            for metric_id, label, aggregate in (
                (
                    "timing_gap_days_median",
                    "公开指定日到冻结日中位数（0–365 天）",
                    _median(latency),
                ),
                (
                    "timing_gap_days_p90",
                    "公开指定日到冻结日 P90（0–365 天）",
                    _percentile(latency, 0.9),
                ),
                (
                    "timing_gap_days_min",
                    "公开指定日到冻结日最小值（0–365 天）",
                    min(latency) if latency else None,
                ),
                (
                    "timing_gap_days_max",
                    "公开指定日到冻结日最大值（0–365 天）",
                    max(latency) if latency else None,
                ),
                (
                    "timing_gap_same_day_share",
                    "同日比例（分辨率下限，不声称先后顺序）",
                    (
                        Decimal(len(same_day)) / Decimal(len(latency))
                        if latency
                        else None
                    ),
                ),
            ):
                if aggregate is None:
                    rows.append(
                        self._timing_row(
                            token,
                            metric_id,
                            label,
                            unit="share" if metric_id.endswith("_share") else "days",
                            numerator="0",
                            denominator="0",
                            status="not_reportable",
                            note=(
                                "0–365 天子集为空，不写 0；负值与 >365 天单列，"
                                "清单快照发布日不能顶替指定日"
                            ),
                        )
                    )
                    continue
                rows.append(
                    self._timing_row(
                        token,
                        metric_id,
                        label,
                        value=_fmt(aggregate),
                        unit="share" if metric_id.endswith("_share") else "days",
                        numerator=(
                            str(len(same_day))
                            if metric_id.endswith("_share")
                            else str(len(latency))
                        ),
                        denominator=str(len(latency)),
                        note=(
                            "gap = date(block_time_utc) − sanctions_public_timestamp；"
                            "仅在 0–365 天子集上计算，分辨率 1 天，"
                            "|gap| < 1 天只能记同日"
                        ),
                    )
                )
        return rows

    # -------------------------------------------------------------- outflows
    def _outflow_row(
        self,
        issuer: str,
        setting: str,
        metric_id: str,
        label: str,
        *,
        value: str = "",
        unit: str = "token",
        numerator: str = "",
        denominator: str = "",
        covered: int = 0,
        not_covered: int = 0,
        status: str = "reported",
        note: str = "",
    ) -> dict:
        return {
            "issuer": issuer,
            "outflow_window": setting,
            "window_hours": str(
                next(hours for hours in WINDOW_HOURS if f"{hours}h" == setting)
            ),
            "metric_id": metric_id,
            "metric_label": label,
            "value": value,
            "unit": unit,
            "numerator": numerator,
            "denominator": denominator,
            "denominator_definition": (
                "窗口被转账快照覆盖、且仍在 outflow 子集内的事件数"
                "（未覆盖窗口的事件被排除并计数，不当作 0）"
            ),
            "window_covered_events": str(covered),
            "window_not_covered_events": str(not_covered),
            "coverage_scope": COVERAGE_SCOPE,
            "status": status,
            "statement_type": STATEMENT_TYPE,
            "note": note,
        }

    def outflow_stats(
        self, items: list[dict], setting: str, snapshot: TransferSnapshot | None = None
    ) -> dict:
        """Outflow aggregates for one issuer, one window setting."""
        snap = snapshot if snapshot is not None else self.transfers
        hours = next(hours for hours in WINDOW_HOURS if f"{hours}h" == setting)
        covered: list[dict] = []
        not_covered: list[dict] = []
        stats = {
            "subset_events": len(items),
            "covered": 0,
            "not_covered": 0,
            "events_with_outflow": 0,
            "transfers": 0,
            "amount": Decimal(0),
            "source_available": bool(snap and snap.available),
        }
        for item in items:
            event = item["event"]
            event_time = parse_utc(event["block_time_utc"])
            window_start = event_time - timedelta(hours=hours)
            if not snap or not snap.covers(window_start, event_time):
                not_covered.append(item)
                continue
            covered.append(item)
            transfers = outflow_transfers(
                snap.rows_for(event["affected_address"]), window_start, event_time
            )
            decimals = int(event["token_decimals"])
            if transfers:
                stats["events_with_outflow"] += 1
                stats["transfers"] += len(transfers)
                stats["amount"] += sum(
                    _token_amount(row["amount_raw"], decimals) for row in transfers
                )
        stats["covered"] = len(covered)
        stats["not_covered"] = len(not_covered)
        return stats

    def _outflow_reason(self, stats: dict) -> str:
        if not stats["subset_events"]:
            return "outflow 子集为空：没有事件同时具备带日期的交易所标签与冻结前窗口"
        if not stats["source_available"]:
            return (
                "没有转账日志快照（transfer_logs_not_collected）："
                "无法区分「窗口内没有流出」与「没有查」，因此不写 0"
            )
        if stats["covered"] == 0:
            return (
                "该窗口未被转账快照覆盖（window_not_covered）："
                f"{stats['not_covered']} 条子集事件的窗口起点早于快照覆盖，"
                "已排除并计数，不当作 0"
            )
        return ""

    def outflow_metrics(
        self,
        funnel: dict,
        setting: str | None = None,
        snapshot: TransferSnapshot | None = None,
    ) -> list[dict]:
        setting = setting or self.outflow_window
        rows: list[dict] = []
        for token in self.cfg.token_order:
            items = funnel[ANALYSIS_OUTFLOW][token]["items"]
            stats = self.outflow_stats(items, setting, snapshot)
            reason = self._outflow_reason(stats)
            rows.append(
                self._outflow_row(
                    token,
                    setting,
                    "outflow_subset_events",
                    "outflow 子集事件数",
                    value=str(stats["subset_events"]) if stats["subset_events"] else "",
                    unit="events",
                    numerator=str(stats["subset_events"]),
                    covered=stats["covered"],
                    not_covered=stats["not_covered"],
                    status="reported" if stats["subset_events"] else "not_reportable",
                    note=(
                        "子集规模由带日期交易所标签决定；分母事件数不受窗口影响"
                        if stats["subset_events"]
                        else "子集为空：标签层没有带日期来源，任何窗口都无行可算"
                    ),
                )
            )
            measured = stats["covered"] > 0 and stats["source_available"]
            rows.append(
                self._outflow_row(
                    token,
                    setting,
                    "outflow_event_share",
                    "冻结前窗口内有转出的事件占比",
                    value=(
                        share(stats["events_with_outflow"], stats["covered"]) or ""
                        if measured
                        else ""
                    ),
                    unit="share",
                    numerator=str(stats["events_with_outflow"]),
                    denominator=str(stats["covered"]),
                    covered=stats["covered"],
                    not_covered=stats["not_covered"],
                    status="reported" if measured else "not_reportable",
                    note=reason or "描述性：转出行为本身不解释动机或规避",
                )
            )
            rows.append(
                self._outflow_row(
                    token,
                    setting,
                    "outflow_token_total",
                    "冻结前窗口内转出金额合计",
                    value=_fmt(stats["amount"]) if measured else "",
                    unit="token",
                    numerator=str(stats["transfers"]),
                    denominator=str(stats["covered"]),
                    covered=stats["covered"],
                    not_covered=stats["not_covered"],
                    status="reported" if measured else "not_reportable",
                    note=reason or "token 单位（6 位小数）；按事件窗口求和，不与 D1/D2 相加",
                )
            )
        return rows

    # -------------------------------------------------------- frozen balances
    def balance_metrics(self, funnel: dict) -> list[dict]:
        rows: list[dict] = []
        for analysis in ANALYSES:
            for token in self.cfg.token_order:
                items = funnel[analysis][token]["items"]
                values = [
                    _token_amount(
                        item["event"]["balance_pre_event_raw"],
                        int(item["event"]["token_decimals"]),
                    )
                    for item in items
                    if item["event"].get("balance_pre_event_raw")
                ]
                missing = len(items) - len(values)
                for metric_id, label, aggregate in (
                    (
                        "frozen_balance_at_freeze_total",
                        "子集冻结时余额合计（D1）",
                        sum(values, Decimal(0)) if values else None,
                    ),
                    (
                        "frozen_balance_at_freeze_median",
                        "子集冻结时余额中位数（D1）",
                        _median(values),
                    ),
                ):
                    rows.append(
                        {
                            "issuer": token,
                            "subset_id": analysis,
                            "metric_id": metric_id,
                            "metric_label": label,
                            "value": _fmt(aggregate) if aggregate is not None else "",
                            "unit": "token",
                            "numerator": str(len(values)),
                            "denominator": str(len(items)),
                            "denominator_definition": "该子集事件数（分子为有 D1 值的事件数）",
                            "coverage_scope": COVERAGE_SCOPE,
                            "balance_reference": BALANCE_REFERENCE,
                            "status": "reported" if values else "not_reportable",
                            "statement_type": STATEMENT_TYPE,
                            "note": (
                                BALANCE_DEFINITION
                                if values
                                else (
                                    "子集为空，没有 D1 值与分母，不写 0；"
                                    "受限子集不使用 D2"
                                )
                            ),
                        }
                    )
                    if missing and values:
                        rows[-1]["note"] = (
                            f"{BALANCE_DEFINITION}；{missing} 条子集事件缺 D1 值，"
                            "不计入分子也不当作 0"
                        )
        return rows

    # ------------------------------------------------------------ sensitivity
    def _sensitivity_row(
        self,
        setting: str,
        issuer: str,
        metric_id: str,
        *,
        numerator: str = "",
        denominator: str = "",
        value: str = "",
        status: str = "not_reportable",
        baseline_value: str = "",
        baseline_status: str = "not_reportable",
        covered: int = 0,
        not_covered: int = 0,
        note: str = "",
    ) -> dict:
        delta = ""
        if value and baseline_value:
            delta = _fmt(Decimal(value) - Decimal(baseline_value))
        if status != "reported" or baseline_status != "reported":
            # 没有测量就没有"依赖窗口"可谈：这一格不是 no，而是 not_assessable。
            affected = "not_assessable"
        elif setting == BASELINE["outflow_window"]:
            affected = "no"
        else:
            before = Decimal(baseline_value)
            after = Decimal(value)
            if before == 0 and after != 0:
                affected = "yes"
            elif before != 0 and abs((after - before) / before) >= Decimal("0.10"):
                affected = "yes"
            else:
                affected = "no"
        return {
            "parameter": "outflow_window",
            "setting": setting,
            "issuer": issuer,
            "metric_id": metric_id,
            "numerator": numerator,
            "denominator": denominator,
            "value": value,
            "status": status,
            "delta_vs_baseline": delta,
            "conclusion_affected": affected,
            "window_covered_events": str(covered),
            "window_not_covered_events": str(not_covered),
            "statement_type": STATEMENT_TYPE,
            "note": note,
        }

    def sensitivity(
        self, funnel: dict, snapshot: TransferSnapshot | None = None
    ) -> list[dict]:
        rows: list[dict] = []
        baseline = {
            token: self.outflow_stats(
                funnel[ANALYSIS_OUTFLOW][token]["items"],
                BASELINE["outflow_window"],
                snapshot,
            )
            for token in self.cfg.token_order
        }
        for setting in WINDOW_SETTINGS:
            for token in self.cfg.token_order:
                stats = self.outflow_stats(
                    funnel[ANALYSIS_OUTFLOW][token]["items"], setting, snapshot
                )
                base = baseline[token]
                measured = stats["covered"] > 0 and stats["source_available"]
                base_measured = base["covered"] > 0 and base["source_available"]
                share_value = (
                    share(stats["events_with_outflow"], stats["covered"]) or ""
                    if measured
                    else ""
                )
                base_share = (
                    share(base["events_with_outflow"], base["covered"]) or ""
                    if base_measured
                    else ""
                )
                amount_value = _fmt(stats["amount"]) if measured else ""
                base_amount = _fmt(base["amount"]) if base_measured else ""
                shared_note = (
                    "窗口只改变窗口起点的选取；子集与冻结事件不变。"
                    "not_assessable = 该窗口下没有任何可测事件，"
                    "不能读成「结论不受窗口影响」"
                )
                rows.append(
                    self._sensitivity_row(
                        setting,
                        token,
                        "outflow_event_share",
                        numerator=str(stats["events_with_outflow"]),
                        denominator=str(stats["covered"]),
                        value=share_value,
                        status="reported" if measured else "not_reportable",
                        baseline_value=base_share,
                        baseline_status=(
                            "reported" if base_measured else "not_reportable"
                        ),
                        covered=stats["covered"],
                        not_covered=stats["not_covered"],
                        note=shared_note,
                    )
                )
                rows.append(
                    self._sensitivity_row(
                        setting,
                        token,
                        "outflow_token_total",
                        numerator=str(stats["transfers"]),
                        denominator=str(stats["covered"]),
                        value=amount_value,
                        status="reported" if measured else "not_reportable",
                        baseline_value=base_amount,
                        baseline_status=(
                            "reported" if base_measured else "not_reportable"
                        ),
                        covered=stats["covered"],
                        not_covered=stats["not_covered"],
                        note=shared_note,
                    )
                )
        return rows

    # ----------------------------------------------------------------- report
    def report(
        self,
        funnel: dict,
        subset_rows: list[dict],
        timing: list[dict],
        outflow: list[dict],
        balances: list[dict],
        sensitivity: list[dict],
        core_manifest: dict,
        match_manifest: dict,
        events: list[dict],
    ) -> dict:
        timing_sizes = {
            token: self.subset_size(funnel, ANALYSIS_TIMING, token)
            for token in self.cfg.token_order
        }
        outflow_sizes = {
            token: self.subset_size(funnel, ANALYSIS_OUTFLOW, token)
            for token in self.cfg.token_order
        }
        timing_pairs = sum(timing_sizes.values())
        outflow_pairs = sum(outflow_sizes.values())
        index = {(row["metric_id"], row["issuer"], row.get("subset_id", "")): row for row in timing + outflow + balances}

        def value(metric_id: str, issuer: str, subset_id: str = "") -> str:
            row = index.get((metric_id, issuer, subset_id))
            if row is None or row["status"] != "reported" or row["value"] == "":
                return "not_reportable"
            return row["value"]

        def counts(metric_id: str, issuer: str, subset_id: str = "") -> str:
            row = index.get((metric_id, issuer, subset_id))
            if row is None:
                return "0/0"
            return f"{row['numerator']}/{row['denominator']}"

        def count(metric_id: str, issuer: str) -> str:
            row = index.get((metric_id, issuer, ""))
            return row["numerator"] if row is not None else "0"

        def steps_summary(analysis: str) -> dict:
            summary: dict[str, dict] = {}
            for token in self.cfg.token_order:
                steps = funnel[analysis][token]["steps"]
                summary[token] = {
                    "final_subset": int(steps[-1]["events_remaining"]),
                    "dropped_by_step": {
                        step["step_id"]: step["events_dropped"] for step in steps[1:]
                    },
                    "dropped_reason_codes": {
                        step["step_id"]: step["dropped_reason_codes"]
                        for step in steps[1:]
                        if step["dropped_reason_codes"]
                    },
                }
            return summary

        transfer_note = (
            "无转账日志快照"
            if not self.transfers.available
            else f"快照 {self.transfers.path}（{self.transfers.transfers} 条转账）"
        )

        conclusions = [
            {
                "id": "R1",
                "statement_type": STATEMENT_TYPE,
                "analysis": "subset",
                "coverage_scope": COVERAGE_SCOPE,
                "metric_ids": [],
                "text": (
                    "受限子集由匹配证据与日期充分性界定，逐级筛选见 "
                    "subset_eligibility.csv：timing-gap 子集 USDT "
                    f"{timing_sizes['USDT']}、USDC {timing_sizes['USDC']}；"
                    f"exchange-outflow 子集 USDT {outflow_sizes['USDT']}、USDC "
                    f"{outflow_sizes['USDC']}（这两个子集互不合并，也不与核心集相加）。"
                ),
                "guards": ["subset_rules_recheckable", "separation_from_core"],
            },
            {
                "id": "R2",
                "statement_type": STATEMENT_TYPE,
                "analysis": ANALYSIS_TIMING,
                "coverage_scope": COVERAGE_SCOPE,
                "metric_ids": [
                    "timing_gap_events",
                    "timing_gap_days_median",
                    "timing_gap_days_p90",
                    "timing_gap_same_day_share",
                    "timing_gap_freeze_before_designation_events",
                    "timing_gap_address_added_later_events",
                ],
                "text": (
                    (
                        "timing-gap 子集为空（0 条），因此公开指定日到冻结日的时间差不可报告："
                        "没有事件同时具备 OFAC 匹配证据与实体级公开指定日。"
                        "用清单快照发布日顶替会得到一个错误的「响应速度」。"
                    )
                    if timing_pairs == 0
                    else (
                        "timing-gap 子集 USDT "
                        f"{count('timing_gap_events', 'USDT')}、USDC "
                        f"{count('timing_gap_events', 'USDC')} 个 episode 起点"
                        "（公开指定日为实体级、precision=day）。"
                        "0–365 天子集的中位数：USDT "
                        f"{value('timing_gap_days_median', 'USDT')} 天、USDC "
                        f"{value('timing_gap_days_median', 'USDC')} 天；同日占比 USDT "
                        f"{value('timing_gap_same_day_share', 'USDT')}、USDC "
                        f"{value('timing_gap_same_day_share', 'USDC')}。"
                        "另单列、不进入中位数：冻结早于指定 USDT "
                        f"{count('timing_gap_freeze_before_designation_events', 'USDT')}、USDC "
                        f"{count('timing_gap_freeze_before_designation_events', 'USDC')}；"
                        "指定超过 365 天 USDT "
                        f"{count('timing_gap_address_added_later_events', 'USDT')}、USDC "
                        f"{count('timing_gap_address_added_later_events', 'USDC')}。"
                        "两者都不读作负延迟或响应延迟，也不推断私有执法请求速度。"
                    )
                ),
                "guards": [
                    "no_snapshot_date_as_sanctions_date",
                    "no_private_request_inference",
                    "latency_buckets_separated",
                ],
            },
            {
                "id": "R3",
                "statement_type": STATEMENT_TYPE,
                "analysis": ANALYSIS_OUTFLOW,
                "coverage_scope": COVERAGE_SCOPE,
                "metric_ids": ["outflow_event_share", "outflow_token_total"],
                "text": (
                    (
                        "exchange-outflow 子集为空（0 条），因此转出统计不可报告："
                        "交易所标签层没有带日期的历史来源，"
                        f"且{transfer_note}。"
                        "本数据集不回答「冻结前资金是否到达交易所」（2026-11-18 决定），"
                        "理由见 docs/restricted-subset-analyses.md §8；"
                        "「不可报告」不等于「没有转出」。"
                    )
                    if outflow_pairs == 0
                    else (
                        "exchange-outflow 子集内、"
                        f"{self.outflow_window} 窗口：占比 USDT "
                        f"{value('outflow_event_share', 'USDT')}、USDC "
                        f"{value('outflow_event_share', 'USDC')}；转出金额合计 USDT "
                        f"{value('outflow_token_total', 'USDT')}、USDC "
                        f"{value('outflow_token_total', 'USDC')} token。"
                    )
                ),
                "guards": [
                    "transfer_absence_not_zero",
                    "no_evasion_inference",
                    "missing_labels_not_zero",
                ],
            },
            {
                "id": "R4",
                "statement_type": STATEMENT_TYPE,
                "analysis": "frozen_balances",
                "coverage_scope": COVERAGE_SCOPE,
                "metric_ids": [
                    "frozen_balance_at_freeze_total",
                    "frozen_balance_at_freeze_median",
                ],
                "text": (
                    "受限子集的 frozen balances 只用 D1（at_freeze）："
                    f"timing-gap 子集 USDT "
                    f"{value('frozen_balance_at_freeze_total', 'USDT', ANALYSIS_TIMING)}、"
                    f"USDC {value('frozen_balance_at_freeze_total', 'USDC', ANALYSIS_TIMING)}；"
                    f"exchange-outflow 子集 USDT "
                    f"{value('frozen_balance_at_freeze_total', 'USDT', ANALYSIS_OUTFLOW)}、"
                    f"USDC {value('frozen_balance_at_freeze_total', 'USDC', ANALYSIS_OUTFLOW)}"
                    "（token 单位）。D2 是地址级、截止日口径，不在本子集报告，"
                    "两者不相加也不互换。"
                ),
                "guards": ["balances_one_definition", "no_enforcement_effectiveness_inference"],
            },
            {
                "id": "R5",
                "statement_type": STATEMENT_TYPE,
                "analysis": ANALYSIS_OUTFLOW,
                "coverage_scope": COVERAGE_SCOPE,
                "metric_ids": ["outflow_event_share", "outflow_token_total"],
                "text": (
                    "outflow 窗口按 "
                    f"{', '.join(WINDOW_SETTINGS)} 逐一对照（基线 "
                    f"{BASELINE['outflow_window']}），见 "
                    "outflow_window_sensitivity.csv。"
                    + (
                        "本次所有窗口都是 not_assessable：没有可测事件，"
                        "所以「结论不随窗口变化」不成立，只能说明窗口选择尚无影响。"
                        if all(
                            row["conclusion_affected"] == "not_assessable"
                            for row in sensitivity
                        )
                        else "对照里出现 yes 的行表示该指标随窗口变化，结论须按窗口分别表述。"
                    )
                ),
                "guards": ["window_dependence_disclosed"],
            },
            {
                "id": "R6",
                "statement_type": STATEMENT_TYPE,
                "analysis": "separation",
                "coverage_scope": COVERAGE_SCOPE,
                "metric_ids": [],
                "text": (
                    "本文件的所有数字只属于受限子集：不与核心事件集、"
                    "也不与工单 07 的 comparison/differences 拼接或互相补充；"
                    "本模块不产出 issuer 差值，因此没有「USDT 减去 USDC」的行。"
                ),
                "guards": ["separation_from_core", "not_extrapolated"],
            },
        ]

        return {
            "scope": {
                "core_id": core_manifest["core_id"],
                "core_data_digest": core_manifest["digests"]["data_digest"],
                "match_batch_id": match_manifest.get("match_batch_id", ""),
                "match_data_digest": match_manifest["digests"]["data_digest"],
                "observation_window": {
                    "start_block": core_manifest["scope"]["observation_start_block"],
                    "cutoff_block": core_manifest["scope"]["study_cutoff_block"],
                },
                "tokens": core_manifest["scope"]["tokens"],
                "core_events": len(events),
                "transfers": {
                    "path": self.transfers.path,
                    "sha256": self.transfers.sha256,
                    "available": self.transfers.available,
                    "transfers": self.transfers.transfers,
                    "coverage": {
                        "start_block": self.transfers.start_block,
                        "end_block": self.transfers.end_block,
                        "start_block_time_utc": self.transfers.start_time_utc,
                        "end_block_time_utc": self.transfers.end_time_utc,
                    },
                },
            },
            "tables": {
                "subset_eligibility_rows": len(subset_rows),
                "timing_gap_rows": len(timing),
                "exchange_outflow_rows": len(outflow),
                "frozen_balance_rows": len(balances),
                "sensitivity_rows": len(sensitivity),
                "sensitivity_not_assessable": sum(
                    1 for row in sensitivity if row["conclusion_affected"] == "not_assessable"
                ),
            },
            "parameters": {
                "baseline": dict(BASELINE),
                "active": {"outflow_window": self.outflow_window},
                "grid": {"outflow_window": list(WINDOW_SETTINGS)},
                "design": (
                    "outflow window 是唯一参数，一次只改一个设定，其余保持基线；"
                    "每行与基线逐项对照。conclusion_affected = not_assessable "
                    "表示该窗口下没有可测事件（没有测量，就谈不上窗口依赖）"
                ),
            },
            "subset_rules": {
                "timing_gap": {
                    "steps": [
                        {"step_id": step_id, "rule": rule, "dropped_as": fallback}
                        for step_id, rule, fallback in TIMING_STEPS
                    ],
                    "result": steps_summary(ANALYSIS_TIMING),
                    "exit_condition": (
                        "必须同时有 ofac_sdn matched 与 sanctions_public_timestamp"
                        "（含 sanctions_date_basis）；快照发布日不算制裁日期（R6）"
                    ),
                },
                "exchange_outflow": {
                    "steps": [
                        {"step_id": step_id, "rule": rule, "dropped_as": fallback}
                        for step_id, rule, fallback in OUTFLOW_STEPS
                    ],
                    "result": steps_summary(ANALYSIS_OUTFLOW),
                    "exit_condition": (
                        "必须有 exchange_label matched 且 label_date 不晚于事件日期"
                        "（R5/前视保护）；无日期标签只进 missing-label 敏感性，不进本子集"
                    ),
                },
            },
            "subsets": {
                "timing_gap": timing_sizes,
                "exchange_outflow": outflow_sizes,
                "reported_total": timing_pairs + outflow_pairs,
            },
            "separation": {
                "concatenated_with_core": False,
                "cross_issuer_difference_produced": False,
                "core_comparison_views_read": [],
                "table_names": list(TABLES),
                "not_concatenated_with": list(CORE_COMPARISON_VIEWS),
                "coverage_scope_of_every_row": COVERAGE_SCOPE,
                "note": (
                    "受限分析写在独立批次目录、用独立表名，每个指标行都带 "
                    "coverage_scope = restricted_subset；本模块不读工单 07 的比较表，"
                    "也不产出 issuer 差值，因此无法在没有说明的情况下被拼进核心结论"
                ),
            },
            "balance_definition": {
                "balance_reference": BALANCE_REFERENCE,
                "definition": BALANCE_DEFINITION,
                "d2_reported_on_subset": False,
                "reason": (
                    "D2 是「截止日仍冻结地址」的地址级口径，"
                    "与「事件是否落在受限子集」不是同一粒度；两者混用会产生口径差"
                ),
            },
            "conclusions": conclusions,
            "coverage_statement": (
                "本批次每个指标都属于受限子集（coverage_scope = restricted_subset）："
                f"timing-gap 子集 {timing_pairs} 条、exchange-outflow 子集 "
                f"{outflow_pairs} 条（合计 {timing_pairs + outflow_pairs} 条），"
                f"分母之外的 {len(events)} 条核心事件不参与任何一个受限指标，"
                "也不作为它们的对照。"
            ),
            "caveats": {
                "descriptive_only": (
                    "全部数字都是描述性统计，不含因果推断；子集内外都不解释原因"
                ),
                "no_private_request_inference": (
                    "私有执法请求时间不可观测（not_observable），无对应字段；"
                    "公开制裁时间差不是请求响应速度"
                ),
                "no_evasion_inference": (
                    "不得由冻结前转出推断规避行为；转出统计只描述资金移动"
                ),
                "no_enforcement_effectiveness_inference": (
                    "不得由冻结余额或时间差推断执法有效性"
                ),
                "not_extrapolated": (
                    "受限子集的数字不得外推到全部冻结活动；"
                    "子集内外的事件数、余额与比例不能相加或合并"
                ),
                "subset_rules_recheckable": (
                    "子集由 subset_eligibility.csv 的逐级筛选界定："
                    "规则文字、每级进/出/剩余与原因码都在表里，可逐步复核"
                ),
                "no_snapshot_date_as_sanctions_date": (
                    "OFAC 快照只给清单发布日，写入 label_date（list_snapshot_date）；"
                    "它不写 sanctions_public_timestamp，也不得用于计算时间差"
                ),
                "missing_labels_not_zero": (
                    "未尝试匹配、未匹配、无日期标签一律留空并带原因码；"
                    "不读成 0，也不读成「不属于交易所」"
                ),
                "transfer_absence_not_zero": (
                    "没有转账日志快照、或窗口未被快照覆盖时，outflow 指标为 "
                    "not_reportable / 被排除计数，不写 0"
                ),
                "balances_one_definition": (
                    "受限子集只用 D1（at_freeze）；D2 是地址级截止日口径，"
                    "不在本子集报告，两者不相加、不互换"
                ),
                "separation_from_core": (
                    "受限分析与核心分析是两条路径：独立批次、独立表名、"
                    "coverage_scope = restricted_subset，且不产出 issuer 差值"
                ),
                "window_dependence_disclosed": (
                    "outflow window 是参数，逐设定对照都写进 "
                    "outflow_window_sensitivity.csv；not_assessable 不是「不受影响」"
                ),
            },
            "rule_checks": self.rules_self_test(),
        }

    # --------------------------------------------------------------- rule test
    def rules_self_test(self) -> dict:
        """Assert the guards that keep the restricted path honest.

        The delivered subset is empty (no dated sanctions timestamp, no dated exchange
        label, no transfer snapshot), so without these assertions the date rules,
        window filtering and zero-vs-absent distinction would be prose that nothing
        exercises. They call the same functions that build the delivered rows.
        """
        results: dict[str, str] = {}

        def fixture_event(
            event_id: str,
            *,
            block_time: str = "2021-06-10T00:00:00Z",
            token: str = "USDT",
            event_class: str = "freezing",
            transition: str = "true",
            before: str = "not_blacklisted",
            balance: str = "1000000",
        ) -> dict:
            return {
                "event_id": event_id,
                "token_symbol": token,
                "analysis_eligibility": "core",
                "event_class": event_class,
                "blacklist_transition": transition,
                "blacklist_state_before": before,
                "affected_address": "0x" + "11" * 20,
                "block_time_utc": block_time,
                "log_order_key": "0000000001:0000:000001",
                "balance_pre_event_raw": balance,
                "token_decimals": "6",
            }

        def ofac_row(event_id: str, **overrides) -> dict:
            row = {
                "event_id": event_id,
                "match_layer": OFAC_LAYER,
                "match_status": "matched",
                "sanctions_public_timestamp": "2021-06-01",
                "sanctions_date_basis": "designation_date",
                "label_date": "2026-11-09",
                "missing_reason_code": "",
            }
            row.update(overrides)
            return row

        def label_row(event_id: str, **overrides) -> dict:
            row = {
                "event_id": event_id,
                "match_layer": LABEL_LAYER,
                "match_status": "matched",
                "label_date": "2021-01-01",
                "missing_reason_code": "",
            }
            row.update(overrides)
            return row

        # 1. matched OFAC without a dated sanctions record is not in the timing subset.
        snapshot_date_only = fixture_event("ev_t1")
        funnel = self.funnel(
            [snapshot_date_only],
            [
                ofac_row(
                    "ev_t1",
                    sanctions_public_timestamp="",
                    sanctions_date_basis="",
                    label_date="2026-11-09",
                    missing_reason_code="label_date_missing",
                ),
                label_row("ev_t1", match_status="not_attempted", label_date=""),
            ],
        )
        results["subset_requires_dated_sanctions_timestamp"] = (
            "ok"
            if self.subset_size(funnel, ANALYSIS_TIMING, "USDT") == 0
            else "failed"
        )

        # 2. a dated sanctions record does enter the subset (rule is not vacuous).
        dated = fixture_event("ev_t2")
        funnel = self.funnel(
            [dated],
            [ofac_row("ev_t2"), label_row("ev_t2", match_status="not_attempted", label_date="")],
        )
        results["dated_sanctions_enters_subset"] = (
            "ok"
            if self.subset_size(funnel, ANALYSIS_TIMING, "USDT") == 1
            else "failed"
        )

        # 3. a label dated after the event never enters the outflow subset.
        fixture_label = fixture_event("ev_o1")
        funnel = self.funnel(
            [fixture_label],
            [
                ofac_row("ev_o1", match_status="unmatched", sanctions_public_timestamp="", sanctions_date_basis=""),
                label_row("ev_o1", label_date="2022-01-01"),
            ],
        )
        results["look_ahead_label_not_in_outflow_subset"] = (
            "ok"
            if self.subset_size(funnel, ANALYSIS_OUTFLOW, "USDT") == 0
            else "failed"
        )

        # 4. a dated label before the event does enter it.
        funnel = self.funnel(
            [fixture_event("ev_o2")],
            [
                ofac_row("ev_o2", match_status="unmatched", sanctions_public_timestamp="", sanctions_date_basis=""),
                label_row("ev_o2"),
            ],
        )
        results["dated_label_enters_subset"] = (
            "ok"
            if self.subset_size(funnel, ANALYSIS_OUTFLOW, "USDT") == 1
            else "failed"
        )

        # 5. the window selects transfers: 2h before the freeze is inside 6h, not 1h.
        items = funnel[ANALYSIS_OUTFLOW]["USDT"]["items"]
        fixture_snapshot = TransferSnapshot(
            path="<rule-check>",
            sha256="",
            available=True,
            start_time_utc="2021-01-01T00:00:00Z",
            end_time_utc="2022-01-01T00:00:00Z",
            transfers_by_address={
                "0x" + "11" * 20: [
                    {
                        "address": "0x" + "11" * 20,
                        "direction": "out",
                        "amount_raw": "250000",
                        "block_time_utc": "2021-06-09T22:00:00Z",
                        "log_index": "0",
                    }
                ]
            },
        )
        wide = self.outflow_stats(items, "6h", fixture_snapshot)
        narrow = self.outflow_stats(items, "1h", fixture_snapshot)
        results["window_selects_transfers"] = (
            "ok"
            if wide["events_with_outflow"] == 1
            and narrow["events_with_outflow"] == 0
            and wide["amount"] == Decimal("0.25")
            else "failed"
        )

        # 6. a window the snapshot does not cover is excluded, not read as zero.
        uncovered = TransferSnapshot(
            path="<rule-check>",
            sha256="",
            available=True,
            start_time_utc="2021-06-09T23:30:00Z",
            end_time_utc="2022-01-01T00:00:00Z",
            transfers_by_address={},
        )
        stats = self.outflow_stats(items, "24h", uncovered)
        results["uncovered_window_is_not_zero"] = (
            "ok"
            if stats["covered"] == 0 and stats["not_covered"] == 1
            else "failed"
        )

        # 7. no snapshot at all: not_reportable, not a zero rate.
        missing = TransferSnapshot(
            path="<rule-check>", sha256="", available=False
        )
        stats = self.outflow_stats(items, "24h", missing)
        row = self._outflow_row(
            "USDT",
            "24h",
            "outflow_event_share",
            "冻结前窗口内有转出的事件占比",
            numerator=str(stats["events_with_outflow"]),
            denominator=str(stats["covered"]),
            covered=stats["covered"],
            not_covered=stats["not_covered"],
            status="reported"
            if stats["covered"] > 0 and stats["source_available"]
            else "not_reportable",
            note=self._outflow_reason(stats),
        )
        results["missing_transfer_source_is_not_reportable"] = (
            "ok"
            if row["status"] == "not_reportable"
            and row["value"] == ""
            and "transfer_logs_not_collected" in row["note"]
            else "failed"
        )

        # 8. a covered window with no transfer is a real zero, not a missing value.
        empty_but_covered = TransferSnapshot(
            path="<rule-check>",
            sha256="",
            available=True,
            start_time_utc="2021-01-01T00:00:00Z",
            end_time_utc="2022-01-01T00:00:00Z",
            transfers_by_address={},
        )
        stats = self.outflow_stats(items, "24h", empty_but_covered)
        results["covered_window_without_transfer_is_zero"] = (
            "ok"
            if stats["covered"] == 1
            and stats["events_with_outflow"] == 0
            and share(stats["events_with_outflow"], stats["covered"]) == "0.000000"
            else "failed"
        )

        # 9. the restricted frozen balance uses D1 of the subset events only.
        balances = self.balance_metrics(
            self.funnel(
                [fixture_event("ev_b1")],
                [
                    ofac_row("ev_b1"),
                    label_row("ev_b1", match_status="not_attempted", label_date=""),
                ],
            )
        )
        d1_rows = [
            row
            for row in balances
            if row["subset_id"] == ANALYSIS_TIMING and row["issuer"] == "USDT"
        ]
        results["frozen_balance_is_d1_only"] = (
            "ok"
            if d1_rows
            and all(row["balance_reference"] == "at_freeze" for row in d1_rows)
            and all("D2" in row["note"] or row["status"] == "not_reportable" for row in d1_rows)
            else "failed"
        )

        # 10. an empty subset yields not_reportable rows, never a zero.
        empty_funnel = self.funnel([fixture_event("ev_e1")], [
            ofac_row("ev_e1", match_status="unmatched", sanctions_public_timestamp="", sanctions_date_basis=""),
            label_row("ev_e1", match_status="not_attempted", label_date=""),
        ])
        timing_rows = self.timing_metrics(empty_funnel)
        results["empty_subset_is_not_a_zero"] = (
            "ok"
            if all(
                row["status"] == "not_reportable" and row["value"] == ""
                for row in timing_rows
            )
            else "failed"
        )

        # 11. window sensitivity marks "no measurement" as not_assessable.
        sensitivity = self.sensitivity(empty_funnel)
        results["window_sensitivity_not_assessable_when_unmeasured"] = (
            "ok"
            if sensitivity
            and all(
                row["conclusion_affected"] == "not_assessable"
                and row["status"] == "not_reportable"
                for row in sensitivity
            )
            else "failed"
        )

        # 12. with a measurement present the window table must actually move, and the
        #     wide/narrow pair must be labelled as an affected conclusion.
        window_sensitivity = self.sensitivity(funnel, fixture_snapshot)
        narrow = next(
            row
            for row in window_sensitivity
            if row["setting"] == "1h"
            and row["metric_id"] == "outflow_token_total"
            and row["issuer"] == "USDT"
        )
        wide = next(
            row
            for row in window_sensitivity
            if row["setting"] == "6h"
            and row["metric_id"] == "outflow_token_total"
            and row["issuer"] == "USDT"
        )
        results["window_sensitivity_reacts_to_window"] = (
            "ok"
            if narrow["value"] == "0.000000"
            and wide["value"] == "0.250000"
            and narrow["delta_vs_baseline"] == "-0.250000"
            and narrow["conclusion_affected"] == "yes"
            and wide["conclusion_affected"] == "no"
            else "failed"
        )

        # 13. the restricted tables cannot be mistaken for the comparison views.
        results["restricted_tables_do_not_collide_with_core_views"] = (
            "ok" if not set(TABLES) & set(CORE_COMPARISON_VIEWS) else "failed"
        )
        return results

    # ------------------------------------------------------------------ runner
    def run(self) -> dict:
        core_dir, core_manifest, events, match_dir, match_manifest, matches = (
            self.load_inputs()
        )
        funnel = self.funnel(events, matches)
        subset_rows = self.subset_rows(funnel)
        timing = self.timing_metrics(funnel)
        outflow = self.outflow_metrics(funnel)
        balances = self.balance_metrics(funnel)
        sensitivity = self.sensitivity(funnel)
        failed = {
            rule: status
            for rule, status in self.rules_self_test().items()
            if status != "ok"
        }
        if failed:
            raise SystemExit(f"受限分析规则自检失败：{sorted(failed)}")
        report = self.report(
            funnel,
            subset_rows,
            timing,
            outflow,
            balances,
            sensitivity,
            core_manifest,
            match_manifest,
            events,
        )
        return {
            "tables": {
                SUBSET_TABLE: subset_rows,
                TIMING_TABLE: timing,
                OUTFLOW_TABLE: outflow,
                BALANCE_TABLE: balances,
                SENSITIVITY_TABLE: sensitivity,
            },
            "report": report,
            "core_dir": core_dir,
            "core_manifest": core_manifest,
            "match_dir": match_dir,
            "match_manifest": match_manifest,
            "events": events,
            "matches": matches,
        }

    # --------------------------------------------------------------- delivery
    def run_id(self, core_manifest: dict, match_manifest: dict) -> str:
        return "restrict_" + sha256_hex(
            canonical_json(
                {
                    "core_data_digest": core_manifest["digests"]["data_digest"],
                    "match_data_digest": match_manifest["digests"]["data_digest"],
                    "transfers_sha256": self.transfers.sha256,
                    "dictionary_sha256": sha256_hex(
                        self.cfg.dictionary_csv.read_bytes()
                    ),
                    "dataset_contract_version": self.cfg.registry[
                        "dataset_contract_version"
                    ],
                    "parameters": {"outflow_window": self.outflow_window},
                }
            ).encode()
        )[:32]

    def deliver(self, outcome: dict) -> dict:
        for table, rows in outcome["tables"].items():
            _assert_fields(table, FIELD_ORDER[table], rows)

        run_id = self.run_id(outcome["core_manifest"], outcome["match_manifest"])
        out_dir = self.cfg.out_root / run_id
        out_dir.mkdir(parents=True, exist_ok=True)

        written: list[Path] = []
        parquet_written: dict[str, bool] = {}
        for table, rows in outcome["tables"].items():
            fields = FIELD_ORDER[table]
            csv_path = out_dir / f"{table}.csv"
            write_csv(csv_path, fields, rows)
            written.append(csv_path)
            parquet_written[table] = write_parquet(
                out_dir / f"{table}.parquet", fields, rows
            )
        report_path = out_dir / "report.json"
        report_path.write_text(
            json.dumps(outcome["report"], indent=1, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        manifest = {
            "restrict_batch_id": run_id,
            "dataset_contract_version": self.cfg.registry["dataset_contract_version"],
            "generated_at_utc": now_utc(),
            "parameters": outcome["report"]["parameters"],
            "inputs": {
                "core_id": outcome["core_manifest"]["core_id"],
                "core_dir": self.cfg.rel(outcome["core_dir"]),
                "core_data_digest": outcome["core_manifest"]["digests"]["data_digest"],
                "match_batch_id": outcome["match_manifest"].get("match_batch_id", ""),
                "match_dir": self.cfg.rel(outcome["match_dir"]),
                "match_data_digest": outcome["match_manifest"]["digests"]["data_digest"],
                "transfers": self.transfers.path,
                "transfers_sha256": self.transfers.sha256,
                "dictionary": self.cfg.rel(self.cfg.dictionary_csv),
            },
            "counts": {
                "subset_eligibility_rows": len(outcome["tables"][SUBSET_TABLE]),
                "timing_gap_rows": len(outcome["tables"][TIMING_TABLE]),
                "exchange_outflow_rows": len(outcome["tables"][OUTFLOW_TABLE]),
                "frozen_balance_rows": len(outcome["tables"][BALANCE_TABLE]),
                "sensitivity_rows": len(outcome["tables"][SENSITIVITY_TABLE]),
                "timing_gap_subset": outcome["report"]["subsets"]["timing_gap"],
                "exchange_outflow_subset": outcome["report"]["subsets"][
                    "exchange_outflow"
                ],
            },
            "separation": outcome["report"]["separation"],
            "report": {
                "path": self.cfg.rel(report_path),
                "conclusions": [row["id"] for row in outcome["report"]["conclusions"]],
            },
            "checks": {
                "columns": (
                    "五张派生分析视图在写出前按本模块声明的字段断言"
                    "（不进字典表，也不进入任何采集表）"
                ),
                "rules": outcome["report"]["rule_checks"],
                "offline_recheck": "python scripts/reconstruct_restricted.py --check",
            },
            "columns_verified": True,
            "parquet_written": parquet_written,
            "digests": {
                "data_digest": data_digest(outcome["tables"]),
                "artifact_digest": artifact_digest(written + [report_path]),
                "data_digest_excludes": {
                    table: sorted(PROVENANCE_TIME_FIELDS.get(table, set()))
                    for table in outcome["tables"]
                },
            },
            "notes": self.transfers.notes,
            "reproduce_command": (
                "python scripts/reconstruct_restricted.py"
                f" --outflow-window {self.outflow_window}"
            ),
            "outputs": {
                table: self.cfg.rel(out_dir / f"{table}.csv")
                for table in outcome["tables"]
            }
            | {"report": self.cfg.rel(report_path)},
        }
        manifest_path = out_dir / "manifest.json"
        if manifest_path.exists():
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
            if (
                previous.get("digests", {}).get("data_digest")
                == manifest["digests"]["data_digest"]
                and previous.get("generated_at_utc")
            ):
                manifest["generated_at_utc"] = previous["generated_at_utc"]
        manifest["last_verified_at_utc"] = now_utc()
        manifest_path.write_text(
            json.dumps(manifest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        manifest["manifest_path"] = self.cfg.rel(manifest_path)
        return manifest

    # ------------------------------------------------------------------- check
    def check(self) -> list[str]:
        """Offline re-check: rebuild the five views and re-assert every guard."""
        problems: list[str] = []
        try:
            (
                core_dir,
                core_manifest,
                events,
                match_dir,
                match_manifest,
                matches,
            ) = self.load_inputs()
        except SystemExit as exc:
            return [str(exc)]

        run_id = self.run_id(core_manifest, match_manifest)
        out_dir = self.cfg.out_root / run_id
        manifest_path = out_dir / "manifest.json"
        if not manifest_path.exists():
            return [f"受限分析产物缺失：{self.cfg.rel(manifest_path)}"]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("restrict_batch_id") != run_id:
            problems.append(
                f"manifest.restrict_batch_id={manifest.get('restrict_batch_id')} "
                f"与输入派生的 {run_id} 不一致"
            )
        active = manifest.get("parameters", {}).get("active", {})
        if active.get("outflow_window") != self.outflow_window:
            problems.append(
                f"manifest 的 outflow_window={active.get('outflow_window')!r} "
                f"与本次复核参数 {self.outflow_window!r} 不一致"
            )
        if problems:
            return problems

        # 1. upstream bundles must be byte-identical to their own manifests.
        core_tables = {
            table: read_table(core_dir / f"{table}.csv")
            for table in ("events", "balances", "validation", "chain_registry")
        }
        if data_digest(core_tables) != core_manifest["digests"]["data_digest"]:
            problems.append("核心集表被改动：受限分析层不允许写回核心集")
        if data_digest({"matches": matches}) != match_manifest["digests"]["data_digest"]:
            problems.append("matches.csv 重算的数据摘要与匹配层 manifest 不一致")

        # 2. rebuild all five views and compare row by row.
        funnel = self.funnel(events, matches)
        rebuilt = {
            SUBSET_TABLE: self.subset_rows(funnel),
            TIMING_TABLE: self.timing_metrics(funnel),
            OUTFLOW_TABLE: self.outflow_metrics(funnel),
            BALANCE_TABLE: self.balance_metrics(funnel),
            SENSITIVITY_TABLE: self.sensitivity(funnel),
        }
        for table, rows in rebuilt.items():
            try:
                _assert_fields(table, FIELD_ORDER[table], rows)
            except ValueError as exc:
                problems.append(str(exc))
                continue
            delivered = read_table(out_dir / f"{table}.csv")
            if delivered != rows:
                problems.append(f"{table}.csv 与按输入重建的结果不一致")
        if data_digest(rebuilt) != manifest["digests"]["data_digest"]:
            problems.append("受限分析表重算的数据摘要与 manifest 不一致")

        report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
        recomputed = self.report(
            funnel,
            rebuilt[SUBSET_TABLE],
            rebuilt[TIMING_TABLE],
            rebuilt[OUTFLOW_TABLE],
            rebuilt[BALANCE_TABLE],
            rebuilt[SENSITIVITY_TABLE],
            core_manifest,
            match_manifest,
            events,
        )
        for key, value in recomputed.items():
            if report.get(key) != value:
                problems.append(f"report.json 的 {key} 与重算结果不一致")
        for caveat in REQUIRED_CAVEATS:
            if not report.get("caveats", {}).get(caveat):
                problems.append(f"report.json 缺少陈述：{caveat}")
        for rule, status in recomputed["rule_checks"].items():
            if status != "ok":
                problems.append(f"规则自检失败：{rule}")

        # 3. the subset funnel must add up, or the boundary is not re-checkable.
        for row in rebuilt[SUBSET_TABLE]:
            if int(row["events_in"]) - int(row["events_dropped"]) != int(
                row["events_remaining"]
            ):
                problems.append(
                    f"{row['analysis']}/{row['issuer']}/{row['step_id']} 的筛选链算术不一致"
                )
            if row["events_in"] == "0" and row["dropped_reason_codes"]:
                problems.append(
                    f"{row['analysis']}/{row['issuer']}/{row['step_id']} 无输入却记了淘汰原因"
                )
        for analysis in ANALYSES:
            for token in self.cfg.token_order:
                steps = [
                    row
                    for row in rebuilt[SUBSET_TABLE]
                    if row["analysis"] == analysis and row["issuer"] == token
                ]
                remainders = [int(row["events_remaining"]) for row in steps]
                if remainders != sorted(remainders, reverse=True):
                    problems.append(f"{analysis}/{token} 的筛选链出现回流")
                if report["subsets"][analysis][token] != remainders[-1]:
                    problems.append(f"{analysis}/{token} 的子集规模与筛选链末级不一致")

        # 4. row-level invariants: descriptive, restricted, no invented values.
        for table, rows in rebuilt.items():
            for row in rows:
                if row["statement_type"] != "descriptive":
                    problems.append(f"{table} 有行未标为 descriptive")
                if "coverage_scope" in row and row["coverage_scope"] != COVERAGE_SCOPE:
                    problems.append(f"{table} 有行未标为 restricted_subset")
                if "status" in row:
                    if row["status"] not in STATUSES:
                        problems.append(f"{table} 有行的状态不在枚举内")
                    if row["status"] == "reported" and row["value"] == "":
                        problems.append(f"{table} 有行报告状态却没有值")
                    if row["status"] == "not_reportable" and row["value"] != "":
                        problems.append(f"{table} 有行不可报告却给出了值")
                if table == BALANCE_TABLE and row["balance_reference"] != BALANCE_REFERENCE:
                    problems.append("受限子集出现了非 D1 的余额口径")
        for row in rebuilt[SENSITIVITY_TABLE]:
            if row["conclusion_affected"] not in {"yes", "no", "not_assessable"}:
                problems.append("敏感性行出现了非法结论标记")
            if row["parameter"] != "outflow_window":
                problems.append("敏感性行出现了非 outflow_window 的参数")
            if row["setting"] not in WINDOW_SETTINGS:
                problems.append("敏感性行出现了不在网格内的窗口")
            if row["conclusion_affected"] == "not_assessable" and row["status"] == "reported":
                problems.append("敏感性行有测量却标为 not_assessable")
        for row in rebuilt[OUTFLOW_TABLE]:
            covered = int(row["window_covered_events"])
            if row["status"] == "not_reportable" and covered and row["value"] != "":
                problems.append("outflow 行不可报告却给出了值")
            if int(row["window_not_covered_events"]) < 0 or covered < 0:
                problems.append("outflow 行的窗口覆盖计数为负")

        # 5. separation: the restricted bundle must not touch the core comparison views.
        separation = report["separation"]
        if separation["concatenated_with_core"] is not False:
            problems.append("report 未声明与核心结论分离")
        if separation["cross_issuer_difference_produced"] is not False:
            problems.append("受限分析层不应产出 issuer 差值")
        if set(TABLES) & set(CORE_COMPARISON_VIEWS):
            problems.append("受限分析表名与工单 07 的视图重名")
        for view in CORE_COMPARISON_VIEWS:
            if (out_dir / f"{view}.csv").exists():
                problems.append(f"受限分析批次目录出现了核心比较视图 {view}.csv")

        if manifest.get("separation") != separation:
            problems.append("manifest.separation 与 report 不一致")
        report_path = self.cfg.path(manifest["report"]["path"])
        if report_path != out_dir / "report.json":
            problems.append("manifest.report.path 未指向本批次的 report.json")
        return problems
