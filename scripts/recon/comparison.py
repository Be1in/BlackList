"""Issuer comparison under one scope, with parameterised sensitivity (ticket 07).

The comparison answers only one question: given the *same* observation window, the
*same* event definitions and the *same* missing-value treatment, what does the
delivered core event set say about USDT versus USDC? It never re-derives events or
balances; it reads the ticket-05 bundle and the ticket-06 match bundle and refuses to
produce a comparison when the preconditions are not met.

Preconditions, all re-checked offline by ``--check``:

``same_observation_window``
    Every event sits inside the window fixed by ``config/study_scope.json``, the core
    manifest declares that same window, both issuers are present and every event's
    chain is a declared in-scope chain.
``same_event_definition``
    ``event_class`` comes from the declared enum, the union of observed classes is
    exactly the declared set, and the dictionary/contract version are the ones the
    core bundle was built from (the bundle id is re-derived from current inputs).
``same_missingness_treatment``
    Both issuers are filtered with the same eligibility expression (``core``) and the
    filter drops no row for either issuer; exclusion rule sets are identical; every
    balance used by a metric is ``complete``; the match layer declares no exclusions.
``same_balance_definition``
    D1 (``at_freeze``) rows are exactly the episode-start freezing events, D2
    (``at_study_cutoff``) rows are all still blacklisted, and both issuers use one
    ``balance_source``.
``same_match_layer``
    The match bundle was built from this core bundle (same id and data digest) and
    covers every event with both layers.

When any precondition fails the run stops with a non-zero exit and writes nothing:
a comparison produced under unequal treatment would look like a data difference.

Sensitivity is one-at-a-time around the baseline
(``timestamp_precision = second``, ``missing_labels = drop_undated``):

* ``timestamp_precision``: ``second`` vs ``day``. Counts cannot move; episode
  durations do, because a day-precision interval is a whole number of days.
* ``missing_labels``: ``drop_undated`` (denominator = events with attempted labels)
  vs ``retain_unlabelled`` (denominator = all core events, numerator unchanged).
  On the delivered data the label registry has no source, so both settings stay
  ``not_reportable``; when dated labels exist the two denominators are reported and
  compared. A missing label is never read as a zero or as "not an exchange".

Every metric row carries its coverage scope (``core_full`` for the whole retrievable
event set, ``core_closed_episodes`` for the closed-episode durations,
``restricted_subset`` for analyses that need match evidence and a complete label
date, ``structural_zero`` for an event class the contract cannot emit) and is marked
``descriptive``. The report refuses to pass ``--check`` if a conclusion is not
descriptive, if a non-comparable pair is differenced, or if a required caveat is
missing.
"""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from .config import Config
from .core import DECISION_PATH, locate_core_bundle
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

COMPARISON_TABLE = "comparison"
DIFFERENCE_TABLE = "differences"
SENSITIVITY_TABLE = "sensitivity"
TABLES = (COMPARISON_TABLE, DIFFERENCE_TABLE, SENSITIVITY_TABLE)

COMPARISON_FIELDS = [
    "metric_id",
    "metric_label",
    "issuer",
    "event_class",
    "value",
    "unit",
    "numerator",
    "denominator",
    "denominator_definition",
    "coverage_scope",
    "balance_reference",
    "status",
    "comparable",
    "comparability_note",
    "statement_type",
]
DIFFERENCE_FIELDS = [
    "metric_id",
    "metric_label",
    "unit",
    "value_usdt",
    "value_usdc",
    "difference_usdt_minus_usdc",
    "difference_status",
    "not_comparable_reason",
    "coverage_scope",
    "same_observation_window",
    "same_event_definition",
    "same_missingness_treatment",
    "interpretation_guard",
]
SENSITIVITY_FIELDS = [
    "parameter",
    "setting",
    "metric_id",
    "issuer",
    "numerator",
    "denominator",
    "value",
    "status",
    "delta_vs_baseline",
    "conclusion_affected",
    "note",
]
FIELD_ORDER = {
    COMPARISON_TABLE: COMPARISON_FIELDS,
    DIFFERENCE_TABLE: DIFFERENCE_FIELDS,
    SENSITIVITY_TABLE: SENSITIVITY_FIELDS,
}

BASELINE = {"timestamp_precision": "second", "missing_labels": "drop_undated"}
PRECISION_SETTINGS = ("second", "day")
MISSING_LABEL_SETTINGS = ("drop_undated", "retain_unlabelled")
ELIGIBLE = "core"
COVERAGE_CORE = "core_full"
COVERAGE_CLOSED_EPISODES = "core_closed_episodes"
COVERAGE_RESTRICTED = "restricted_subset"
COVERAGE_STRUCTURAL = "structural_zero"
METRIC_STATUSES = {"reported", "not_reportable", "structural_zero"}
DIFFERENCE_STATUSES = {"comparable", "not_comparable"}
OFAC_LAYER = "ofac_sdn"
LABEL_LAYER = "exchange_label"
ATTEMPTED_STATUSES = frozenset({"matched", "unmatched"})
# 反馈只指定这两个 issuer；代币集合变了就应先重做研究期决策，而不是静默少一行比较。
ISSUER_PAIR = ("USDT", "USDC")
# Declared by docs/definitions.md 2.3: USDC has no issuer-initiated destruction
# entry point on Ethereum. This is an event-definition fact, not an observed zero.
STRUCTURAL_ZERO = {"USDC": {"destruction"}}

METRICS = (
    ("events_total", "事件总数", "events", COVERAGE_CORE),
    ("freezing_events", "freezing 事件数", "events", COVERAGE_CORE),
    ("unfreezing_events", "unfreezing 事件数", "events", COVERAGE_CORE),
    ("destruction_events", "destruction 事件数", "events", COVERAGE_CORE),
    ("episode_starts", "episode 起点数（状态真的转为冻结）", "episodes", COVERAGE_CORE),
    ("non_transition_freezing", "状态未改变的 freezing 日志数（R9）", "events", COVERAGE_CORE),
    ("unique_addresses", "事件涉及地址数（去重）", "addresses", COVERAGE_CORE),
    (
        "addresses_still_blacklisted_at_cutoff",
        "截止日仍冻结地址数",
        "addresses",
        COVERAGE_CORE,
    ),
    (
        "closed_episodes",
        "已闭合 episode 数",
        "episodes",
        COVERAGE_CLOSED_EPISODES,
    ),
    ("open_episodes_at_cutoff", "截止日仍开放的 episode 数", "episodes", COVERAGE_CORE),
    (
        "episode_duration_days_median",
        "episode 时长中位数",
        "days",
        COVERAGE_CLOSED_EPISODES,
    ),
    (
        "episode_duration_days_p90",
        "episode 时长 P90",
        "days",
        COVERAGE_CLOSED_EPISODES,
    ),
    (
        "episode_duration_days_min",
        "episode 时长最小值",
        "days",
        COVERAGE_CLOSED_EPISODES,
    ),
    (
        "frozen_balance_at_freeze_total",
        "冻结时余额合计（D1）",
        "token",
        COVERAGE_CORE,
    ),
    (
        "frozen_balance_at_freeze_median",
        "冻结时余额中位数（D1）",
        "token",
        COVERAGE_CORE,
    ),
    (
        "frozen_balance_at_cutoff_total",
        "截止日仍冻结余额合计（D2）",
        "token",
        COVERAGE_CORE,
    ),
    (
        "frozen_balance_at_cutoff_median",
        "截止日仍冻结余额中位数（D2）",
        "token",
        COVERAGE_CORE,
    ),
    (
        "ofac_match_rate_events",
        "OFAC 公共记录匹配率（事件口径）",
        "share",
        COVERAGE_CORE,
    ),
    (
        "exchange_label_dated_coverage",
        "带日期交易所标签覆盖率",
        "share",
        COVERAGE_RESTRICTED,
    ),
)
METRIC_IDS = tuple(metric[0] for metric in METRICS)

REQUIRED_CAVEATS = (
    "descriptive_only",
    "no_private_request_inference",
    "no_evasion_inference",
    "no_enforcement_effectiveness_inference",
    "not_extrapolated",
    "destruction_structural_zero",
    "same_scope_precondition",
    "missing_labels_not_zero",
    "timestamp_precision_disclosed",
    "snapshot_postdates_events",
    "balances_two_definitions",
    "closed_episodes_only",
)

DIFFERENCE_GUARDS = {
    "destruction_events": (
        "USDC 的 destruction 为结构性零：差值来自事件定义（合约无 issuer 销毁入口），"
        "不是冻结行为差异"
    ),
    "ofac_match_rate_events": (
        "公共记录覆盖差异；禁止读成冻结行为、规避或执法有效性差异，"
        "且 OFAC 快照晚于全部事件"
    ),
    "frozen_balance_at_freeze_total": (
        "描述性余额统计（D1）；禁止推断执法有效性或冻结动机"
    ),
    "frozen_balance_at_freeze_median": (
        "描述性余额统计（D1）；禁止推断执法有效性或冻结动机"
    ),
    "frozen_balance_at_cutoff_total": (
        "描述性余额统计（D2）；禁止推断执法有效性或冻结动机，"
        "D2 不得与 D1 混用"
    ),
    "frozen_balance_at_cutoff_median": (
        "描述性余额统计（D2）；禁止推断执法有效性或冻结动机，"
        "D2 不得与 D1 混用"
    ),
    "episode_duration_days_median": (
        "描述性时长统计；禁止推断规避行为或私有执法请求的响应速度"
    ),
    "episode_duration_days_p90": (
        "描述性时长统计；禁止推断规避行为或私有执法请求的响应速度"
    ),
    "episode_duration_days_min": (
        "描述性时长统计；时间戳精度见 sensitivity.csv，禁止推断更细的先后顺序"
    ),
}
DEFAULT_GUARD = "描述性差异；共同观察期、事件定义与缺失处理已在本次运行中核验一致"


def parse_utc(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


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


def is_episode_start(event: dict) -> bool:
    """A freeze that actually entered the blacklisted state (R9 logs excluded)."""
    return (
        event["event_class"] == "freezing"
        and event["blacklist_transition"] == "true"
        and event["blacklist_state_before"] == "not_blacklisted"
    )


def is_closing_unfreeze(event: dict) -> bool:
    """An unfreeze that actually left the blacklisted state (R9 logs excluded)."""
    return (
        event["event_class"] == "unfreezing"
        and event["blacklist_transition"] == "true"
        and event["blacklist_state_before"] == "blacklisted"
    )


class ComparisonPreconditionError(SystemExit):
    """The two issuers are not on the same scope; no comparison may be produced."""


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
class ComparisonRun:
    """Read the core + match bundles and write the issuer comparison bundle."""

    cfg: Config
    core_id: str = ""
    timestamp_precision: str = BASELINE["timestamp_precision"]
    missing_labels: str = BASELINE["missing_labels"]
    dictionary: dict[str, list[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.timestamp_precision not in PRECISION_SETTINGS:
            raise SystemExit(
                f"timestamp_precision={self.timestamp_precision!r} 不在 "
                f"{PRECISION_SETTINGS} 内"
            )
        if self.missing_labels not in MISSING_LABEL_SETTINGS:
            raise SystemExit(
                f"missing_labels={self.missing_labels!r} 不在 "
                f"{MISSING_LABEL_SETTINGS} 内"
            )
        self.dictionary = load_dictionary(self.cfg.dictionary_csv)

    # ----------------------------------------------------------------- inputs
    def decision(self) -> tuple[dict, str]:
        raw = self.cfg.path(DECISION_PATH).read_bytes()
        return json.loads(raw.decode("utf-8")), sha256_hex(raw)

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
    ) -> tuple[Path, dict, list[dict], list[dict], Path, dict, list[dict]]:
        core_dir, core_manifest = locate_core_bundle(
            self.cfg, self.core_id, require_current_inputs=True
        )
        events = read_table(core_dir / "events.csv")
        balances = read_table(core_dir / "balances.csv")
        assert_columns("events", self.dictionary["events"], events)
        assert_columns("balances", self.dictionary["balances"], balances)
        match_dir, match_manifest, matches = self.match_inputs(core_manifest)
        return (
            core_dir,
            core_manifest,
            events,
            balances,
            match_dir,
            match_manifest,
            matches,
        )

    # ---------------------------------------------------------- preconditions
    def _window_check(
        self,
        events: list[dict],
        core_manifest: dict,
        decision: dict,
        tokens: list[str],
    ) -> dict:
        start = int(decision["observation_start_block"])
        cutoff = int(decision["study_cutoff_block"])
        per_token = {
            token: {
                "events": sum(1 for event in events if event["token_symbol"] == token),
                "min_block": min(
                    (
                        int(event["block_number"])
                        for event in events
                        if event["token_symbol"] == token
                    ),
                    default=None,
                ),
                "max_block": max(
                    (
                        int(event["block_number"])
                        for event in events
                        if event["token_symbol"] == token
                    ),
                    default=None,
                ),
            }
            for token in tokens
        }
        outside = [
            event["event_id"]
            for event in events
            if not start <= int(event["block_number"]) <= cutoff
        ]
        manifest_window_ok = (
            int(core_manifest["scope"]["observation_start_block"]) == start
            and int(core_manifest["scope"]["study_cutoff_block"]) == cutoff
        )
        chains = decision.get("scope", {}).get("chains", [])
        chains_ok = (
            core_manifest["scope"].get("chains") == chains
            and {event["chain"] for event in events} == set(chains)
        )
        tokens_ok = (
            tokens == list(ISSUER_PAIR)
            and {event["token_symbol"] for event in events} == set(tokens)
            and all(bucket["events"] > 0 for bucket in per_token.values())
        )
        missing_time = [
            event["event_id"] for event in events if not event["block_time_utc"]
        ]
        return {
            "passed": not outside
            and manifest_window_ok
            and chains_ok
            and tokens_ok
            and not missing_time,
            "observation_start_block": start,
            "study_cutoff_block": cutoff,
            "events_per_token": per_token,
            "events_outside_window": len(outside),
            "outside_event_ids": outside[:10],
            "events_without_block_time": len(missing_time),
            "core_manifest_window": {
                "start_block": core_manifest["scope"]["observation_start_block"],
                "cutoff_block": core_manifest["scope"]["study_cutoff_block"],
            },
            "chains": chains,
            "detail": (
                "每个事件的 block_number 都在决策窗口内且区块时间可用；"
                "核心 manifest 声明同一窗口；链、代币范围与决策一致，两个 issuer 都有事件"
            ),
        }

    def _definition_check(
        self, events: list[dict], decision: dict, core_manifest: dict
    ) -> dict:
        declared = decision.get("scope", {}).get("event_classes", [])
        observed = sorted({event["event_class"] for event in events})
        by_token_class = Counter(
            (event["token_symbol"], event["event_class"]) for event in events
        )
        return {
            "passed": observed == sorted(declared),
            "observed_event_classes": observed,
            "declared_event_classes": declared,
            "by_token_and_class": {
                f"{token}/{event_class}": count
                for (token, event_class), count in sorted(by_token_class.items())
            },
            "dictionary": self.cfg.rel(self.cfg.dictionary_csv),
            "dataset_contract_version": core_manifest["dataset_contract_version"],
            "detail": (
                "三类事件互斥且来自同一声明；字典与契约版本和核心集一致"
                "（核心批次 id 在读取输入时已按当前日志快照/决策/字典核对）"
            ),
        }

    def _missingness_check(
        self,
        events: list[dict],
        balances: list[dict],
        match_manifest: dict,
        tokens: list[str],
    ) -> dict:
        rules: dict[str, list[str]] = {}
        eligibility: dict[str, dict] = {}
        dropped: dict[str, int] = {}
        for token in tokens:
            evs = [event for event in events if event["token_symbol"] == token]
            rules[token] = sorted(
                {event["exclusion_rule_id"] for event in evs if event["exclusion_rule_id"]}
            )
            eligibility[token] = dict(
                sorted(Counter(event["analysis_eligibility"] for event in evs).items())
            )
            dropped[token] = sum(
                1 for event in evs if event["analysis_eligibility"] != ELIGIBLE
            )
        balance_status = {
            token: dict(
                sorted(
                    Counter(
                        row["missingness_status"]
                        for row in balances
                        if row["token_symbol"] == token
                    ).items()
                )
            )
            for token in tokens
        }
        used_incomplete = sum(
            1
            for row in balances
            if row["balance_reference"] in {"at_freeze", "at_study_cutoff"}
            and row["missingness_status"] != "complete"
        )
        same_rule_set = len({tuple(value) for value in rules.values()}) == 1
        no_drops = all(count == 0 for count in dropped.values())
        separation = match_manifest.get("separation", {})
        separation_ok = (
            separation.get("match_based_exclusions") == 0
            and separation.get("eligibility_unchanged") is True
        )
        return {
            "passed": same_rule_set and no_drops and not used_incomplete and separation_ok,
            "eligibility_filter": f"analysis_eligibility == {ELIGIBLE!r}（两个 issuer 同一表达式）",
            "eligibility_by_token": eligibility,
            "dropped_by_filter": dropped,
            "exclusion_rules_by_token": rules,
            "balance_missingness_by_token": balance_status,
            "metric_balances_incomplete": used_incomplete,
            "match_separation": separation,
            "detail": (
                "两个 issuer 用同一资格表达式与同一排除规则集；过滤不丢行；"
                "指标用到的余额全部 complete；匹配层声明 0 条按匹配剔除"
            ),
        }

    def _balance_definition_check(
        self, events: list[dict], balances: list[dict]
    ) -> dict:
        starts = {
            event["event_id"] for event in events if is_episode_start(event)
        }
        d1_refs = {
            row["reference_event_id"]
            for row in balances
            if row["balance_reference"] == "at_freeze"
        }
        by_token_reference = Counter(
            (row["token_symbol"], row["balance_reference"]) for row in balances
        )
        d2_state_ok = all(
            row["blacklist_state_at_balance_block"] == "blacklisted"
            for row in balances
            if row["balance_reference"] == "at_study_cutoff"
        )
        # 指标会直接消费这两个字段；空值必须在进入比较前被挡住，不能静默缩小分母。
        d1_values_ok = all(
            event["balance_pre_event_raw"]
            for event in events
            if event["event_id"] in starts
        )
        d2_values_ok = all(
            row["balance_raw"]
            for row in balances
            if row["balance_reference"] == "at_study_cutoff"
        )
        sources = sorted({row["balance_source"] for row in balances})
        references = sorted({row["balance_reference"] for row in balances})
        return {
            "passed": d1_refs == starts
            and d1_values_ok
            and d2_state_ok
            and d2_values_ok
            and len(sources) == 1,
            "d1_rows_match_episode_starts": d1_refs == starts,
            "d1_values_present": d1_values_ok,
            "d2_values_present": d2_values_ok,
            "episode_starts": len(starts),
            "d1_rows": len(d1_refs),
            "d2_still_blacklisted": d2_state_ok,
            "balance_references": references,
            "balance_sources": sources,
            "by_token_and_reference": {
                f"{token}/{reference}": count
                for (token, reference), count in sorted(by_token_reference.items())
            },
            "detail": (
                "D1 只出现在 episode 起点；D2 只出现在截止日仍 blacklisted 的地址；"
                "两个 issuer 共用同一余额来源（归档状态）"
            ),
        }

    def _match_layer_check(
        self, events: list[dict], matches: list[dict], core_manifest: dict, match_manifest: dict
    ) -> dict:
        covered = Counter(row["event_id"] for row in matches)
        layers = sorted({row["match_layer"] for row in matches})
        inputs = match_manifest.get("inputs", {})
        return {
            "passed": (
                inputs.get("core_id") == core_manifest["core_id"]
                and inputs.get("core_data_digest")
                == core_manifest["digests"]["data_digest"]
                and set(covered) == {event["event_id"] for event in events}
                and all(count == 2 for count in covered.values())
                and layers == [LABEL_LAYER, OFAC_LAYER]
            ),
            "match_batch_id": match_manifest.get("match_batch_id"),
            "core_id": inputs.get("core_id"),
            "core_data_digest": inputs.get("core_data_digest"),
            "layers": layers,
            "rows": len(matches),
            "events_covered": len(covered),
            "detail": (
                "匹配层由同一核心集生成（批次、数据摘要一致），每个事件两层各一行；"
                "匹配结果不参与剔除"
            ),
        }

    def preconditions(
        self,
        events: list[dict],
        balances: list[dict],
        matches: list[dict],
        core_manifest: dict,
        match_manifest: dict,
        decision: dict,
    ) -> dict:
        tokens = list(self.cfg.token_order)
        checks = {
            "same_observation_window": self._window_check(
                events, core_manifest, decision, tokens
            ),
            "same_event_definition": self._definition_check(
                events, decision, core_manifest
            ),
            "same_missingness_treatment": self._missingness_check(
                events, balances, match_manifest, tokens
            ),
            "same_balance_definition": self._balance_definition_check(events, balances),
            "same_match_layer": self._match_layer_check(
                events, matches, core_manifest, match_manifest
            ),
        }
        return {
            "checks": checks,
            "failed": [name for name, check in checks.items() if not check["passed"]],
            "passed": all(check["passed"] for check in checks.values()),
        }

    # ---------------------------------------------------------------- metrics
    def _duration(self, freeze: dict, close: dict, precision: str) -> Decimal:
        if precision == "day":
            days = parse_utc(close["block_time_utc"]).date() - parse_utc(
                freeze["block_time_utc"]
            ).date()
            return Decimal(days.days)
        seconds = (
            parse_utc(close["block_time_utc"]) - parse_utc(freeze["block_time_utc"])
        ).total_seconds()
        return Decimal(seconds) / Decimal(86400)

    def _episodes(
        self, events: list[dict], token: str
    ) -> tuple[list[tuple[dict, dict]], int]:
        """Pair each episode start with its closing unfreeze; count open episodes.

        Only state-changing rows participate: a repeated freezing log (R9) cannot
        open an episode and a repeated unfreezing log cannot close one. An unfreeze
        whose freezing happened before the observation window has no in-window start
        and is not paired, which is why the table reports closed and open counts.
        """
        starts = [
            event
            for event in events
            if event["token_symbol"] == token and is_episode_start(event)
        ]
        closes = [
            event
            for event in events
            if event["token_symbol"] == token and is_closing_unfreeze(event)
        ]
        by_address: dict[str, list[dict]] = defaultdict(list)
        for event in starts + closes:
            by_address[event["affected_address"]].append(event)
        pairs: list[tuple[dict, dict]] = []
        open_episodes = 0
        for sequence in by_address.values():
            sequence.sort(key=lambda event: event["log_order_key"])
            pending: dict | None = None
            for event in sequence:
                if event["event_class"] == "freezing":
                    if pending is not None:
                        open_episodes += 1
                    pending = event
                elif pending is not None:
                    pairs.append((pending, event))
                    pending = None
            if pending is not None:
                open_episodes += 1
        return pairs, open_episodes

    def _row(
        self,
        metric_id: str,
        label: str,
        issuer: str,
        *,
        value: str = "",
        unit: str = "",
        numerator: str = "",
        denominator: str = "",
        denominator_definition: str = "",
        coverage_scope: str = COVERAGE_CORE,
        balance_reference: str = "",
        status: str = "reported",
        comparable: str = "yes",
        note: str = "",
        event_class: str = "",
    ) -> dict:
        return {
            "metric_id": metric_id,
            "metric_label": label,
            "issuer": issuer,
            "event_class": event_class,
            "value": value,
            "unit": unit,
            "numerator": numerator,
            "denominator": denominator,
            "denominator_definition": denominator_definition,
            "coverage_scope": coverage_scope,
            "balance_reference": balance_reference,
            "status": status,
            "comparable": comparable,
            "comparability_note": note,
            "statement_type": "descriptive",
        }

    def _structural_zero_row(
        self, metric_id: str, label: str, issuer: str, unit: str
    ) -> dict:
        return self._row(
            metric_id,
            label,
            issuer,
            value="0",
            unit=unit,
            numerator="0",
            denominator="0",
            denominator_definition="不适用：该类事件在本合约上结构性不存在",
            coverage_scope=COVERAGE_STRUCTURAL,
            status="structural_zero",
            comparable="no",
            note=(
                "USDC 的 Ethereum 合约没有 issuer 发起的销毁入口，0 是定义性取值；"
                "不与 USDT 的 destruction 计数比较"
            ),
            event_class="destruction",
        )

    def _label_coverage_row(
        self,
        issuer: str,
        label_rows: list[dict],
        denominator: int,
        denominator_definition: str,
    ) -> dict:
        dated = [
            row
            for row in label_rows
            if row["match_status"] == "matched" and row["label_date"]
        ]
        attempted = [
            row for row in label_rows if row["match_status"] in ATTEMPTED_STATUSES
        ]
        if not attempted:
            # 一次都没尝试就报 0 等于把"没查"说成"查了没有"，所以不论分母取
            # 已尝试行还是全部事件，状态一律不可报告，只登记分母供对照。
            return self._row(
                "exchange_label_dated_coverage",
                "带日期交易所标签覆盖率",
                issuer,
                unit="share",
                numerator=str(len(dated)),
                denominator=str(denominator),
                denominator_definition=denominator_definition,
                coverage_scope=COVERAGE_RESTRICTED,
                status="not_reportable",
                comparable="no",
                note=(
                    "没有带日期的历史交易所标签来源：标签层未尝试，"
                    "不写 0 比率；分母只用于对照，任何依赖交易所标签的 issuer 比较不可报告"
                ),
            )
        value = share(len(dated), denominator)
        return self._row(
            "exchange_label_dated_coverage",
            "带日期交易所标签覆盖率",
            issuer,
            value=value or "",
            unit="share",
            numerator=str(len(dated)),
            denominator=str(denominator),
            denominator_definition=denominator_definition,
            coverage_scope=COVERAGE_RESTRICTED,
            status="reported" if value is not None else "not_reportable",
            comparable="yes" if value is not None else "no",
            note=(
                "覆盖率只说明能匹配到带日期标签的比例；"
                "缺失标签不读成零，也不读成“不属于交易所”"
            ),
        )

    def metrics(
        self, events: list[dict], balances: list[dict], matches: list[dict]
    ) -> list[dict]:
        rows: list[dict] = []
        tokens = list(self.cfg.token_order)
        labels = {
            metric_id: (label, unit, coverage)
            for metric_id, label, unit, coverage in METRICS
        }

        for token in tokens:
            evs = [
                event
                for event in events
                if event["token_symbol"] == token
                and event["analysis_eligibility"] == ELIGIBLE
            ]
            starts = [event for event in evs if is_episode_start(event)]
            non_transition = [
                event
                for event in evs
                if event["event_class"] == "freezing"
                and event["blacklist_transition"] == "false"
            ]
            pairs, open_episodes = self._episodes(evs, token)
            durations = [
                self._duration(freeze, close, self.timestamp_precision)
                for freeze, close in pairs
            ]
            d1_values = [
                _token_amount(event["balance_pre_event_raw"], int(event["token_decimals"]))
                for event in starts
                if event["balance_pre_event_raw"]
            ]
            d2_rows = [
                row
                for row in balances
                if row["token_symbol"] == token
                and row["balance_reference"] == "at_study_cutoff"
            ]
            d2_values = [
                _token_amount(row["balance_raw"], int(row["token_decimals"]))
                for row in d2_rows
            ]
            event_ids = {event["event_id"] for event in evs}
            label_rows = [
                row
                for row in matches
                if row["match_layer"] == LABEL_LAYER and row["event_id"] in event_ids
            ]

            def add(
                metric_id: str,
                value: str,
                *,
                numerator: str = "",
                denominator: str = "",
                denominator_definition: str = "",
                balance_reference: str = "",
                status: str = "reported",
                comparable: str = "yes",
                note: str = "",
                event_class: str = "",
            ) -> None:
                label, unit, coverage = labels[metric_id]
                rows.append(
                    self._row(
                        metric_id,
                        label,
                        token,
                        value=value,
                        unit=unit,
                        numerator=numerator,
                        denominator=denominator,
                        denominator_definition=denominator_definition,
                        coverage_scope=coverage,
                        balance_reference=balance_reference,
                        status=status,
                        comparable=comparable,
                        note=note,
                        event_class=event_class,
                    )
                )

            counts = Counter(event["event_class"] for event in evs)
            unique_addresses = {event["affected_address"] for event in evs}
            add("events_total", str(len(evs)), numerator=str(len(evs)))
            add(
                "freezing_events",
                str(counts["freezing"]),
                numerator=str(counts["freezing"]),
                event_class="freezing",
            )
            add(
                "unfreezing_events",
                str(counts["unfreezing"]),
                numerator=str(counts["unfreezing"]),
                event_class="unfreezing",
            )
            declared_structural = "destruction" in STRUCTURAL_ZERO.get(token, set())
            if declared_structural and counts["destruction"] == 0:
                rows.append(
                    self._structural_zero_row(
                        "destruction_events", labels["destruction_events"][0], token, "events"
                    )
                )
            elif declared_structural:
                # 契约说该类事件结构性不存在，却观测到了：先暴露计数并禁止比较，
                # 不能悄悄写成 0 把矛盾藏起来。
                add(
                    "destruction_events",
                    str(counts["destruction"]),
                    numerator=str(counts["destruction"]),
                    comparable="no",
                    note=(
                        f"定义上结构性零，但观察到 {counts['destruction']} 条 destruction："
                        "需先补验证并重做事件定义，本次不产出该指标的差值"
                    ),
                    event_class="destruction",
                )
            else:
                add(
                    "destruction_events",
                    str(counts["destruction"]),
                    numerator=str(counts["destruction"]),
                    event_class="destruction",
                )
            add(
                "episode_starts",
                str(len(starts)),
                numerator=str(len(starts)),
                note="blacklist_transition = true 且 state_before = not_blacklisted",
            )
            add(
                "non_transition_freezing",
                str(len(non_transition)),
                numerator=str(len(non_transition)),
                note="R9：计入事件覆盖，但不产生 D1、不计入冻结地址数",
                event_class="freezing",
            )
            add("unique_addresses", str(len(unique_addresses)), numerator=str(len(unique_addresses)))
            add(
                "addresses_still_blacklisted_at_cutoff",
                str(len({row["address"] for row in d2_rows})),
                numerator=str(len({row["address"] for row in d2_rows})),
                note="只含截止区块结束时仍 blacklisted 的地址",
            )
            add(
                "closed_episodes",
                str(len(pairs)),
                numerator=str(len(pairs)),
                note="每个 episode 起点后、观察期内的下一个状态改变型 unfreezing",
            )
            add(
                "open_episodes_at_cutoff",
                str(open_episodes),
                numerator=str(open_episodes),
                note="截止日仍未解冻（或解冻前又冻结）的 episode 数",
            )
            for metric_id, aggregate in (
                ("episode_duration_days_median", _median(durations)),
                ("episode_duration_days_p90", _percentile(durations, 0.9)),
                ("episode_duration_days_min", min(durations) if durations else None),
            ):
                if aggregate is None:
                    add(
                        metric_id,
                        "",
                        numerator="0",
                        denominator="0",
                        denominator_definition="已闭合 episode 数",
                        status="not_reportable",
                        comparable="no",
                        note="没有已闭合 episode，无法给出时长",
                    )
                else:
                    add(
                        metric_id,
                        _fmt(aggregate),
                        numerator=str(len(durations)),
                        denominator=str(len(durations)),
                        denominator_definition=(
                            f"已闭合 episode 数；timestamp_precision = "
                            f"{self.timestamp_precision}"
                        ),
                        note=(
                            "时长 = 解冻区块时间 − 冻结区块时间；"
                            f"当前口径为 {self.timestamp_precision}"
                        ),
                    )

            for metric_id, values, reference, definition in (
                (
                    "frozen_balance_at_freeze_total",
                    d1_values,
                    "at_freeze",
                    "episode 起点事件的 balance_pre_event_raw 求和（D1）",
                ),
                (
                    "frozen_balance_at_freeze_median",
                    d1_values,
                    "at_freeze",
                    "episode 起点事件的 balance_pre_event_raw 中位数（D1）",
                ),
                (
                    "frozen_balance_at_cutoff_total",
                    d2_values,
                    "at_study_cutoff",
                    "截止日仍冻结地址的 balanceOf 求和（D2）",
                ),
                (
                    "frozen_balance_at_cutoff_median",
                    d2_values,
                    "at_study_cutoff",
                    "截止日仍冻结地址的 balanceOf 中位数（D2）",
                ),
            ):
                if not values:
                    add(
                        metric_id,
                        "",
                        numerator="0",
                        denominator="0",
                        denominator_definition=definition,
                        balance_reference=reference,
                        status="not_reportable",
                        comparable="no",
                        note="没有可用余额行，不写 0",
                    )
                    continue
                aggregate = (
                    sum(values, Decimal(0))
                    if metric_id.endswith("_total")
                    else _median(values)
                )
                add(
                    metric_id,
                    _fmt(aggregate),
                    numerator=str(len(values)),
                    denominator=str(len(values)),
                    denominator_definition=definition,
                    balance_reference=reference,
                    note=(
                        "token 单位（6 位小数）；两套余额口径分开报告，"
                        "任何指标只使用自己声明的 balance_reference"
                    ),
                )

            ofac_rows = [
                row
                for row in matches
                if row["match_layer"] == OFAC_LAYER and row["event_id"] in event_ids
            ]
            attempted = [
                row
                for row in ofac_rows
                if row["match_status"] in ATTEMPTED_STATUSES
            ]
            matched = [row for row in ofac_rows if row["match_status"] == "matched"]
            rate = share(len(matched), len(attempted))
            add(
                "ofac_match_rate_events",
                rate or "",
                numerator=str(len(matched)),
                denominator=str(len(attempted)),
                denominator_definition="该 issuer 的 OFAC 已尝试匹配事件（matched + unmatched）",
                status="reported" if rate is not None else "not_reportable",
                comparable="yes" if rate is not None else "no",
                note=(
                    "这是公共记录覆盖差异：OFAC 快照晚于全部事件，"
                    "不能读成冻结行为、规避或执法有效性差异"
                ),
            )
            label_attempted = [
                row
                for row in label_rows
                if row["match_status"] in ATTEMPTED_STATUSES
            ]
            rows.append(
                self._label_coverage_row(
                    token,
                    label_rows,
                    len(label_attempted)
                    if self.missing_labels == "drop_undated"
                    else len(evs),
                    (
                        "已尝试的交易所标签匹配行（matched + unmatched）；"
                        "未尝试的行不进分母"
                        if self.missing_labels == "drop_undated"
                        else "该 issuer 的全部核心集事件"
                        "（missing-label 敏感性口径：未标注事件计入分母）"
                    ),
                )
            )
        return rows

    # ------------------------------------------------------------ differences
    def differences(self, metrics: list[dict], preconditions: dict) -> list[dict]:
        index = {(row["metric_id"], row["issuer"]): row for row in metrics}
        checks = preconditions["checks"]
        rows: list[dict] = []
        usdt_token, usdc_token = ISSUER_PAIR
        for metric_id, label, unit, coverage in METRICS:
            usdt = index.get((metric_id, usdt_token))
            usdc = index.get((metric_id, usdc_token))
            if usdt is None or usdc is None:
                continue
            comparable = (
                usdt["comparable"] == "yes"
                and usdc["comparable"] == "yes"
                and usdt["status"] == "reported"
                and usdc["status"] == "reported"
                and usdt["value"] != ""
                and usdc["value"] != ""
            )
            reason = ""
            difference = ""
            if comparable:
                difference = _fmt(Decimal(usdt["value"]) - Decimal(usdc["value"]))
            else:
                parts = [
                    f"{row['issuer']} {row['metric_id']}: "
                    f"status={row['status']}, comparable={row['comparable']}"
                    for row in (usdt, usdc)
                    if row["status"] != "reported" or row["comparable"] != "yes"
                ]
                reason = (
                    "；".join(parts) + "：不可比的指标不产出差值，避免把定义性或缺失差异当成行为差异"
                )
            # 差值行的覆盖范围取两侧中更受限的一侧：结构性零不能说成全量可比。
            scope = (
                COVERAGE_STRUCTURAL
                if COVERAGE_STRUCTURAL in {usdt["coverage_scope"], usdc["coverage_scope"]}
                else coverage
            )
            guard = DIFFERENCE_GUARDS.get(metric_id, DEFAULT_GUARD)
            if metric_id == "destruction_events" and usdc["status"] != "structural_zero":
                guard = (
                    "USDC 观测到 destruction，与事件定义（结构性零）矛盾："
                    "本行只暴露计数、不产出差值，先补验证"
                )
            rows.append(
                {
                    "metric_id": metric_id,
                    "metric_label": label,
                    "unit": unit,
                    "value_usdt": usdt["value"],
                    "value_usdc": usdc["value"],
                    "difference_usdt_minus_usdc": difference,
                    "difference_status": "comparable" if comparable else "not_comparable",
                    "not_comparable_reason": reason,
                    "coverage_scope": scope,
                    "same_observation_window": "yes"
                    if checks["same_observation_window"]["passed"]
                    else "no",
                    "same_event_definition": "yes"
                    if checks["same_event_definition"]["passed"]
                    else "no",
                    "same_missingness_treatment": "yes"
                    if checks["same_missingness_treatment"]["passed"]
                    else "no",
                    "interpretation_guard": guard,
                }
            )
        return rows

    # ------------------------------------------------------------ sensitivity
    def _sensitivity_row(
        self,
        parameter: str,
        setting: str,
        metric_id: str,
        issuer: str,
        *,
        numerator: str = "",
        denominator: str = "",
        value: str = "",
        status: str = "reported",
        baseline_value: str = "",
        baseline_status: str = "reported",
        is_difference: bool = False,
        note: str = "",
    ) -> dict:
        delta = ""
        if value and baseline_value:
            delta = _fmt(Decimal(value) - Decimal(baseline_value))
        affected = "no"
        if status != baseline_status:
            affected = "yes"
        elif value and baseline_value:
            before = Decimal(baseline_value)
            after = Decimal(value)
            sign_flipped = (before > 0) != (after > 0) or (before < 0) != (after < 0)
            if is_difference and sign_flipped:
                affected = "yes"
            elif before != 0 and abs((after - before) / before) >= Decimal("0.10"):
                affected = "yes"
            elif before == 0 and after != 0:
                affected = "yes"
        return {
            "parameter": parameter,
            "setting": setting,
            "metric_id": metric_id,
            "issuer": issuer,
            "numerator": numerator,
            "denominator": denominator,
            "value": value,
            "status": status,
            "delta_vs_baseline": delta,
            "conclusion_affected": affected,
            "note": note,
        }

    def sensitivity(self, events: list[dict], matches: list[dict]) -> list[dict]:
        tokens = list(self.cfg.token_order)
        rows: list[dict] = []

        eligible_events = [
            event for event in events if event["analysis_eligibility"] == ELIGIBLE
        ]

        def stats(token: str, precision: str) -> dict:
            pairs, _ = self._episodes(eligible_events, token)
            durations = [
                self._duration(freeze, close, precision) for freeze, close in pairs
            ]
            return {
                "median": _median(durations),
                "p90": _percentile(durations, 0.9),
                "min": min(durations) if durations else None,
                "count": len(durations),
            }

        usdt_token, usdc_token = ISSUER_PAIR
        baseline = {token: stats(token, "second") for token in tokens}
        baseline_difference = (
            baseline[usdt_token]["median"] - baseline[usdc_token]["median"]
            if baseline[usdt_token]["median"] is not None
            and baseline[usdc_token]["median"] is not None
            else None
        )

        for precision in PRECISION_SETTINGS:
            current = {token: stats(token, precision) for token in tokens}
            for token in tokens:
                for metric_id, key, note in (
                    (
                        "episode_duration_days_median",
                        "median",
                        "时长精度随参数变化；计数不受影响",
                    ),
                    (
                        "episode_duration_days_p90",
                        "p90",
                        "分位数按最近秩（nearest-rank）计算",
                    ),
                    (
                        "episode_duration_days_min",
                        "min",
                        "最小值随精度变化：day 口径下只能取整日，second 口径可见秒级边界；"
                        "本报告不据此下结论",
                    ),
                ):
                    value = current[token][key]
                    base = baseline[token][key]
                    rows.append(
                        self._sensitivity_row(
                            "timestamp_precision",
                            precision,
                            metric_id,
                            token,
                            numerator=str(current[token]["count"]),
                            denominator=str(current[token]["count"]),
                            value=_fmt(value) if value is not None else "",
                            status="reported"
                            if value is not None
                            else "not_reportable",
                            baseline_value=_fmt(base) if base is not None else "",
                            baseline_status="reported"
                            if base is not None
                            else "not_reportable",
                            note=note,
                        )
                    )
                token_events = sum(
                    1
                    for event in eligible_events
                    if event["token_symbol"] == token
                )
                rows.append(
                    self._sensitivity_row(
                        "timestamp_precision",
                        precision,
                        "events_total",
                        token,
                        numerator=str(token_events),
                        denominator=str(token_events),
                        value=str(token_events),
                        baseline_value=str(token_events),
                        note=(
                            "精度参数不进入事件计数，差值为 0 属于口径声明，"
                            "不是经验发现"
                        ),
                    )
                )
            if baseline_difference is not None:
                difference = (
                    current[usdt_token]["median"] - current[usdc_token]["median"]
                    if current[usdt_token]["median"] is not None
                    and current[usdc_token]["median"] is not None
                    else None
                )
                rows.append(
                    self._sensitivity_row(
                        "timestamp_precision",
                        precision,
                        "episode_duration_days_median",
                        "USDT_minus_USDC",
                        value=_fmt(difference) if difference is not None else "",
                        status="reported"
                        if difference is not None
                        else "not_reportable",
                        baseline_value=_fmt(baseline_difference),
                        is_difference=True,
                        note=(
                            "issuer 间差值的对照：精度只改变小数部分，"
                            "差值方向与量级不变"
                        ),
                    )
                )

        label_stats: dict[str, dict] = {}
        for token in tokens:
            evs = [event for event in eligible_events if event["token_symbol"] == token]
            event_ids = {event["event_id"] for event in evs}
            label_rows = [
                row
                for row in matches
                if row["match_layer"] == LABEL_LAYER and row["event_id"] in event_ids
            ]
            attempted = [
                row
                for row in label_rows
                if row["match_status"] in ATTEMPTED_STATUSES
            ]
            dated = [
                row
                for row in label_rows
                if row["match_status"] == "matched" and row["label_date"]
            ]
            baseline_rate = share(len(dated), len(attempted)) if attempted else None
            label_stats[token] = {
                "events": len(evs),
                "attempted": len(attempted),
                "dated": len(dated),
                "baseline_value": baseline_rate or "",
                "baseline_status": (
                    "reported" if baseline_rate is not None else "not_reportable"
                ),
            }

        for setting in MISSING_LABEL_SETTINGS:
            for token in tokens:
                stats = label_stats[token]
                denominator = (
                    stats["events"]
                    if setting == "retain_unlabelled"
                    else stats["attempted"]
                )
                # 与 comparison 表的标签指标同一口径：没尝试就没有比率，不论分母取哪种。
                rate = share(stats["dated"], denominator) if stats["attempted"] else None
                value = rate or ""
                status = "reported" if rate is not None else "not_reportable"
                if not stats["attempted"]:
                    note = (
                        "标签层未尝试（无带日期来源）：两种处理都不可报告；"
                        "参数只改变分母（0 → 全部事件），未标注事件不被读成 0 或“非交易所”"
                    )
                elif setting == "retain_unlabelled":
                    note = (
                        "标签层已尝试：把未标注事件计入分母，分子（带日期标签）不变；"
                        "差值只反映分母口径"
                    )
                else:
                    note = "基线：仅已尝试的标签匹配行进分母"
                rows.append(
                    self._sensitivity_row(
                        "missing_labels",
                        setting,
                        "exchange_label_dated_coverage",
                        token,
                        numerator=str(stats["dated"]),
                        denominator=str(denominator),
                        value=value,
                        status=status,
                        baseline_value=stats["baseline_value"],
                        baseline_status=stats["baseline_status"],
                        note=note,
                    )
                )
                rows.append(
                    self._sensitivity_row(
                        "missing_labels",
                        setting,
                        "events_total",
                        token,
                        numerator=str(stats["events"]),
                        denominator=str(stats["events"]),
                        value=str(stats["events"]),
                        baseline_value=str(stats["events"]),
                        note=(
                            "该参数不进入事件计数，差值为 0 属于口径声明，"
                            "不是经验发现"
                        ),
                    )
                )
        return rows

    # ----------------------------------------------------------------- report
    def report(
        self,
        preconditions: dict,
        metrics: list[dict],
        differences: list[dict],
        sensitivity: list[dict],
        core_manifest: dict,
        match_manifest: dict,
        events: list[dict],
        balances: list[dict],
    ) -> dict:
        index = {(row["metric_id"], row["issuer"]): row for row in metrics}

        def value(metric_id: str, issuer: str) -> str:
            row = index.get((metric_id, issuer))
            if row is None or row["status"] != "reported" or row["value"] == "":
                return "not_reportable"
            return row["value"]

        def counts(metric_id: str, issuer: str) -> str:
            row = index[(metric_id, issuer)]
            return f"{row['numerator']}/{row['denominator']}"

        usdc_destruction = index.get(("destruction_events", "USDC"))
        if usdc_destruction and usdc_destruction["status"] == "structural_zero":
            destruction_clause = (
                "USDC 的 destruction 为 0 属结构性零，"
                "与 USDT 的 destruction 数不构成行为差异。"
            )
        else:
            observed = (
                usdc_destruction["value"] if usdc_destruction is not None else "n/a"
            )
            destruction_clause = (
                f"USDC 观测到 {observed} 条 destruction，与事件定义（结构性零）矛盾："
                "先补验证并重做事件定义，本次不产出该指标的差值。"
            )

        def classes(token: str) -> str:
            parts = []
            for event_class in ("freezing", "unfreezing", "destruction"):
                row = index.get((f"{event_class}_events", token))
                if row is None:
                    continue
                if row["status"] == "structural_zero":
                    parts.append(f"{event_class} 0（结构性零）")
                else:
                    parts.append(f"{event_class} {row['value']}")
            return " / ".join(parts)

        conclusions = [
            {
                "id": "C1",
                "statement_type": "descriptive",
                "issuer_scope": "both",
                "coverage_scope": COVERAGE_CORE,
                "metric_ids": ["events_total"],
                "text": (
                    "比较前置条件全部通过：同一观察期（区块 "
                    f"{core_manifest['scope']['observation_start_block']:,}–"
                    f"{core_manifest['scope']['study_cutoff_block']:,}）、同一事件定义"
                    f"（{', '.join(core_manifest['scope']['event_classes'])}）、"
                    "同一缺失处理（资格过滤 = core，两个 issuer 均无剔除）。"
                    "因此比较表中的差异不是观察期、事件定义或缺失处理造成的。"
                ),
                "guards": ["same_scope_precondition"],
            },
            {
                "id": "C2",
                "statement_type": "descriptive",
                "issuer_scope": "both",
                "coverage_scope": COVERAGE_CORE,
                "metric_ids": [
                    "events_total",
                    "freezing_events",
                    "unfreezing_events",
                    "destruction_events",
                ],
                "text": (
                    f"共同观察期内，USDT 共 {value('events_total', 'USDT')} 条事件"
                    f"（{classes('USDT')}）；USDC 共 {value('events_total', 'USDC')} 条"
                    f"（{classes('USDC')}）。{destruction_clause}"
                ),
                "guards": ["destruction_structural_zero"],
            },
            {
                "id": "C3",
                "statement_type": "descriptive",
                "issuer_scope": "both",
                "coverage_scope": COVERAGE_CORE,
                "metric_ids": [
                    "episode_starts",
                    "non_transition_freezing",
                    "unique_addresses",
                    "addresses_still_blacklisted_at_cutoff",
                ],
                "text": (
                    "状态真的转为冻结的 episode 起点：USDT "
                    f"{value('episode_starts', 'USDT')}、USDC {value('episode_starts', 'USDC')}；"
                    "状态未改变的 freezing 日志（R9，只计覆盖）：USDT "
                    f"{value('non_transition_freezing', 'USDT')}、USDC "
                    f"{value('non_transition_freezing', 'USDC')}；事件涉及地址（去重）：USDT "
                    f"{value('unique_addresses', 'USDT')}、USDC "
                    f"{value('unique_addresses', 'USDC')}；截止日仍冻结地址：USDT "
                    f"{value('addresses_still_blacklisted_at_cutoff', 'USDT')}、USDC "
                    f"{value('addresses_still_blacklisted_at_cutoff', 'USDC')}。"
                ),
                "guards": ["not_extrapolated"],
            },
            {
                "id": "C4",
                "statement_type": "descriptive",
                "issuer_scope": "both",
                "coverage_scope": COVERAGE_CORE,
                "metric_ids": [
                    "frozen_balance_at_freeze_total",
                    "frozen_balance_at_freeze_median",
                    "frozen_balance_at_cutoff_total",
                    "frozen_balance_at_cutoff_median",
                ],
                "text": (
                    "D1（冻结时余额，token 单位）合计：USDT "
                    f"{value('frozen_balance_at_freeze_total', 'USDT')}、USDC "
                    f"{value('frozen_balance_at_freeze_total', 'USDC')}；D2（截止日仍冻结余额）"
                    f"合计：USDT {value('frozen_balance_at_cutoff_total', 'USDT')}、USDC "
                    f"{value('frozen_balance_at_cutoff_total', 'USDC')}。两套口径分别记录，"
                    "不相加、不互换，也不读成执法有效性。"
                ),
                "guards": ["balances_two_definitions", "no_enforcement_effectiveness_inference"],
            },
            {
                "id": "C5",
                "statement_type": "descriptive",
                "issuer_scope": "both",
                "coverage_scope": COVERAGE_CLOSED_EPISODES,
                "metric_ids": [
                    "closed_episodes",
                    "episode_duration_days_median",
                    "episode_duration_days_p90",
                ],
                "text": (
                    "已闭合 episode（冻结 → 解冻）：USDT "
                    f"{value('closed_episodes', 'USDT')} 个、USDC "
                    f"{value('closed_episodes', 'USDC')} 个；时长中位数 USDT "
                    f"{value('episode_duration_days_median', 'USDT')} 天、USDC "
                    f"{value('episode_duration_days_median', 'USDC')} 天，P90 分别为 "
                    f"{value('episode_duration_days_p90', 'USDT')} 天与 "
                    f"{value('episode_duration_days_p90', 'USDC')} 天；"
                    f"截止日仍开放、未参与时长的 episode：USDT "
                    f"{value('open_episodes_at_cutoff', 'USDT')}、USDC "
                    f"{value('open_episodes_at_cutoff', 'USDC')}"
                    "（该计数属 core_full，只说明删失规模）。"
                    "时间戳精度参数（second / day）下的对照见 sensitivity.csv。"
                ),
                "guards": [
                    "no_evasion_inference",
                    "timestamp_precision_disclosed",
                    "closed_episodes_only",
                ],
            },
            {
                "id": "C6",
                "statement_type": "descriptive",
                "issuer_scope": "both",
                "coverage_scope": COVERAGE_CORE,
                "metric_ids": ["ofac_match_rate_events"],
                "text": (
                    "OFAC 公共记录匹配率（事件口径）：USDT "
                    f"{value('ofac_match_rate_events', 'USDT')} "
                    f"（{counts('ofac_match_rate_events', 'USDT')}）、USDC "
                    f"{value('ofac_match_rate_events', 'USDC')} "
                    f"（{counts('ofac_match_rate_events', 'USDC')}）。"
                    "这是公共记录覆盖差异，匹配失败不等于未受制裁；"
                    "快照晚于全部事件，不能用后来的清单解释过去的行为。"
                ),
                "guards": ["coverage_not_extrapolable", "snapshot_postdates_events"],
            },
            {
                "id": "C7",
                "statement_type": "descriptive",
                "issuer_scope": "both",
                "coverage_scope": COVERAGE_RESTRICTED,
                "metric_ids": ["exchange_label_dated_coverage"],
                "text": (
                    "交易所标签层没有带日期的历史来源："
                    f"USDT {value('exchange_label_dated_coverage', 'USDT')}、"
                    f"USDC {value('exchange_label_dated_coverage', 'USDC')}，"
                    "依赖交易所标签的 issuer 比较不可报告；missing-label 敏感性只改变分母，"
                    "不改变核心事件计数。"
                ),
                "guards": ["missing_labels_not_zero", "not_extrapolated"],
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
                "chains": core_manifest["scope"]["chains"],
                "tokens": core_manifest["scope"]["tokens"],
                "event_classes": core_manifest["scope"]["event_classes"],
                "events": len(events),
                "balances": len(balances),
                "matches": match_manifest.get("counts", {}).get("matches", 0),
            },
            "tables": {
                "comparison_rows": len(metrics),
                "difference_rows": len(differences),
                "sensitivity_rows": len(sensitivity),
                "sensitivity_conclusion_affected": sorted(
                    {
                        f"{row['parameter']}={row['setting']}:"
                        f"{row['metric_id']}/{row['issuer']}"
                        for row in sensitivity
                        if row["conclusion_affected"] == "yes"
                    }
                ),
            },
            "parameters": {
                "baseline": dict(BASELINE),
                "active": {
                    "timestamp_precision": self.timestamp_precision,
                    "missing_labels": self.missing_labels,
                },
                "grid": {
                    "timestamp_precision": list(PRECISION_SETTINGS),
                    "missing_labels": list(MISSING_LABEL_SETTINGS),
                },
                "design": (
                    "一次只改一个参数（one-at-a-time），其余保持基线；"
                    "每行与基线逐项对照，见 sensitivity.csv。"
                    "conclusion_affected = yes 当状态改变、issuer 间差值变号，"
                    "或数值相对基线变化 ≥ 10%（基线为 0 时非零即 yes）"
                ),
            },
            "preconditions": preconditions,
            "comparison_eligible": preconditions["passed"],
            "metric_coverage": dict(
                sorted(
                    Counter(row["coverage_scope"] for row in metrics).items()
                )
            ),
            "conclusions": conclusions,
            "coverage_statement": (
                "每个指标在 comparison.csv 的 coverage_scope 标明范围："
                f"core_full = 观察期内全部可检索事件（{len(events)} 条）；"
                "core_closed_episodes = 核心集内已闭合 episode（截止日仍开放的不参与）；"
                "restricted_subset = 需要匹配证据与完整标签日期的子集；"
                "structural_zero = 该合约定义上不存在的事件类。"
                "本文件不把任何子集或结构性零的数值并入 core_full 指标。"
            ),
            "caveats": {
                "descriptive_only": (
                    "所有结论都是描述性统计，不含因果推断；差值只说明数据不同，不说明原因"
                ),
                "no_private_request_inference": (
                    "私有执法请求时间不可观测（not_observable），无对应字段；"
                    "不得由公开时间差或时长推断请求的响应速度"
                ),
                "no_evasion_inference": (
                    "不得由地址流出、解冻时长或冻结余额推断规避行为"
                ),
                "no_enforcement_effectiveness_inference": (
                    "不得由冻结余额或匹配率推断执法有效性"
                ),
                "not_extrapolated": (
                    "有限匹配子集与结构性零不得外推到全部冻结活动；"
                    "restricted_subset 的指标不与核心指标拼接"
                ),
                "destruction_structural_zero": (
                    "USDC 的 Ethereum 合约没有 issuer 发起的销毁入口："
                    "destruction 计数为 0 是定义性取值，已标为 structural_zero 且不产出差值"
                    if usdc_destruction is not None
                    and usdc_destruction["status"] == "structural_zero"
                    else "USDC 观测到 destruction，与结构性零的定义矛盾："
                    "本条改成暴露计数并禁止差值，先补验证再重做事件定义"
                ),
                "same_scope_precondition": (
                    "比较只在同一观察期、同一事件定义、同一缺失处理与同一余额定义下产出；"
                    "任一前置条件不满足时运行直接失败，不写比较结论"
                ),
                "missing_labels_not_zero": (
                    "缺失标签、未尝试匹配、不适用一律留空并带原因码；"
                    "不读成 0，也不读成“不属于交易所”"
                ),
                "timestamp_precision_disclosed": (
                    "事件时间精度为 second，且链上时间戳不建模；"
                    "day 口径把时长截断到整日，两者结果并列在 sensitivity.csv"
                ),
                "snapshot_postdates_events": (
                    "OFAC 快照（publish "
                    f"{match_manifest.get('inputs', {}).get('ofac_publish_date') or 'n/a'}）"
                    "晚于全部事件：匹配只说明该地址在当前清单上"
                ),
                "balances_two_definitions": (
                    "D1（at_freeze）与 D2（at_study_cutoff）分别定义、分别报告；"
                    "任何指标都声明使用的 balance_reference，不混用、不相加"
                ),
                "closed_episodes_only": (
                    "时长指标只用已闭合 episode（冻结后有状态改变型解冻）；"
                    "截止日仍开放的 episode 是删失数据，不参与中位数/P90，"
                    "因此不能读成“冻结通常持续多久”"
                ),
            },
            "rule_checks": self.rules_self_test(),
        }

    # --------------------------------------------------------------- rule test
    def rules_self_test(self) -> dict:
        """Assert the guards that keep the table honest; ``--check`` re-runs them.

        The delivered data has no exclusions, no destroyed USDC balance and no dated
        exchange labels, so without these assertions the guards would be prose that
        nothing exercises. They call the same functions that build the delivered rows.
        """
        results: dict[str, str] = {}
        freeze = {"block_time_utc": "2021-06-01T23:59:00Z"}
        close = {"block_time_utc": "2021-06-02T00:01:00Z"}
        second = self._duration(freeze, close, "second")
        day = self._duration(freeze, close, "day")
        results["day_precision_rounds_to_whole_days"] = (
            "ok" if Decimal(0) < second < Decimal("0.002") and day == Decimal(1) else "failed"
        )

        destruction_label = next(
            label
            for metric_id, label, _unit, _coverage in METRICS
            if metric_id == "destruction_events"
        )
        structural = self._structural_zero_row(
            "destruction_events", destruction_label, "USDC", "events"
        )
        results["structural_zero_not_compared"] = (
            "ok"
            if structural["status"] == "structural_zero"
            and structural["comparable"] == "no"
            and structural["value"] == "0"
            else "failed"
        )

        label = self._label_coverage_row(
            "USDT",
            [
                {
                    "match_status": "not_attempted",
                    "label_date": "",
                }
            ],
            0,
            "已尝试的交易所标签匹配行（matched + unmatched）",
        )
        results["missing_label_is_not_a_zero"] = (
            "ok"
            if label["status"] == "not_reportable"
            and label["value"] == ""
            and label["comparable"] == "no"
            else "failed"
        )

        # 交付的标签注册表为空，missing-label 敏感性在真实数据上只会走"不可报告"分支。
        # 用两个事件 + 一个带日期标签证明参数确实改变分母与数值，而不是写死的两行。
        def fixture_event(event_id: str, block: int, transition: str, before: str) -> dict:
            return {
                "event_id": event_id,
                "token_symbol": "USDT",
                "analysis_eligibility": ELIGIBLE,
                "event_class": "freezing",
                "blacklist_transition": transition,
                "blacklist_state_before": before,
                "affected_address": "0x" + "11" * 20,
                "block_time_utc": f"2021-06-{block:02d}T00:00:00Z",
                "log_order_key": f"{block:010d}:0000:000001",
            }

        fixture_events = [
            fixture_event("ev_sens1", 1, "true", "not_blacklisted"),
            fixture_event("ev_sens2", 2, "false", "blacklisted"),
            {
                **fixture_event("ev_sens3", 3, "true", "not_blacklisted"),
                "token_symbol": "USDC",
            },
        ]
        fixture_matches = [
            {
                "event_id": "ev_sens1",
                "match_layer": LABEL_LAYER,
                "match_status": "matched",
                "label_date": "2021-01-01",
            },
            {
                "event_id": "ev_sens2",
                "match_layer": LABEL_LAYER,
                "match_status": "not_attempted",
                "label_date": "",
            },
            {
                "event_id": "ev_sens3",
                "match_layer": LABEL_LAYER,
                "match_status": "not_attempted",
                "label_date": "",
            },
        ]
        sensitivity = self.sensitivity(fixture_events, fixture_matches)
        retain = next(
            row
            for row in sensitivity
            if row["parameter"] == "missing_labels"
            and row["setting"] == "retain_unlabelled"
            and row["metric_id"] == "exchange_label_dated_coverage"
            and row["issuer"] == "USDT"
        )
        results["missing_label_sensitivity_is_data_driven"] = (
            "ok"
            if retain["value"] == "0.500000"
            and retain["delta_vs_baseline"] == "-0.500000"
            and retain["status"] == "reported"
            and retain["denominator"] == "2"
            else "failed"
        )
        return results

    # ------------------------------------------------------------------ runner
    def run(self) -> dict:
        (
            core_dir,
            core_manifest,
            events,
            balances,
            match_dir,
            match_manifest,
            matches,
        ) = self.load_inputs()
        decision, decision_sha = self.decision()
        preconditions = self.preconditions(
            events,
            balances,
            matches,
            core_manifest,
            match_manifest,
            decision,
        )
        if not preconditions["passed"]:
            raise ComparisonPreconditionError(
                "比较前置条件未满足，不产出比较结论："
                f"{preconditions['failed']}（详见 report 的 preconditions）"
            )
        metrics = self.metrics(events, balances, matches)
        differences = self.differences(metrics, preconditions)
        sensitivity = self.sensitivity(events, matches)
        failed_rules = {
            rule: status
            for rule, status in self.rules_self_test().items()
            if status != "ok"
        }
        if failed_rules:
            raise SystemExit(f"比较规则自检失败：{sorted(failed_rules)}")
        report = self.report(
            preconditions,
            metrics,
            differences,
            sensitivity,
            core_manifest,
            match_manifest,
            events,
            balances,
        )
        return {
            "tables": {
                COMPARISON_TABLE: metrics,
                DIFFERENCE_TABLE: differences,
                SENSITIVITY_TABLE: sensitivity,
            },
            "report": report,
            "core_dir": core_dir,
            "core_manifest": core_manifest,
            "match_dir": match_dir,
            "match_manifest": match_manifest,
            "events": events,
            "balances": balances,
            "matches": matches,
            "decision_sha256": decision_sha,
        }

    # --------------------------------------------------------------- delivery
    def run_id(self, core_manifest: dict, match_manifest: dict, decision_sha: str) -> str:
        return "compare_" + sha256_hex(
            canonical_json(
                {
                    "core_data_digest": core_manifest["digests"]["data_digest"],
                    "match_data_digest": match_manifest["digests"]["data_digest"],
                    "decision_sha256": decision_sha,
                    "dictionary_sha256": sha256_hex(
                        self.cfg.dictionary_csv.read_bytes()
                    ),
                    "dataset_contract_version": self.cfg.registry[
                        "dataset_contract_version"
                    ],
                    "parameters": {
                        "timestamp_precision": self.timestamp_precision,
                        "missing_labels": self.missing_labels,
                    },
                }
            ).encode()
        )[:32]

    def deliver(self, outcome: dict) -> dict:
        for table, rows in outcome["tables"].items():
            _assert_fields(table, FIELD_ORDER[table], rows)

        run_id = self.run_id(
            outcome["core_manifest"],
            outcome["match_manifest"],
            outcome["decision_sha256"],
        )
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
            "compare_batch_id": run_id,
            "dataset_contract_version": self.cfg.registry["dataset_contract_version"],
            "generated_at_utc": now_utc(),
            "parameters": outcome["report"]["parameters"],
            "preconditions": {
                name: check["passed"]
                for name, check in outcome["report"]["preconditions"]["checks"].items()
            },
            "inputs": {
                "core_id": outcome["core_manifest"]["core_id"],
                "core_dir": self.cfg.rel(outcome["core_dir"]),
                "core_data_digest": outcome["core_manifest"]["digests"]["data_digest"],
                "match_batch_id": outcome["match_manifest"].get("match_batch_id", ""),
                "match_dir": self.cfg.rel(outcome["match_dir"]),
                "match_data_digest": outcome["match_manifest"]["digests"]["data_digest"],
                "scope_decision": DECISION_PATH,
                "scope_decision_sha256": outcome["decision_sha256"],
                "dictionary": self.cfg.rel(self.cfg.dictionary_csv),
            },
            "counts": {
                "metrics": len(METRIC_IDS),
                "comparison_rows": len(outcome["tables"][COMPARISON_TABLE]),
                "difference_rows": len(outcome["tables"][DIFFERENCE_TABLE]),
                "sensitivity_rows": len(outcome["tables"][SENSITIVITY_TABLE]),
                "comparable_differences": sum(
                    1
                    for row in outcome["tables"][DIFFERENCE_TABLE]
                    if row["difference_status"] == "comparable"
                ),
            },
            "report": {
                "path": self.cfg.rel(report_path),
                "conclusions": [row["id"] for row in outcome["report"]["conclusions"]],
            },
            "checks": {
                "columns": (
                    "comparison/differences/sensitivity 写出前按本模块声明的字段断言"
                    "（非字典表：派生分析视图，不进入任何采集表）"
                ),
                "preconditions": outcome["report"]["preconditions"],
                "rules": outcome["report"]["rule_checks"],
                "offline_recheck": "python scripts/reconstruct_comparison.py --check",
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
            "reproduce_command": (
                "python scripts/reconstruct_comparison.py"
                f" --timestamp-precision {self.timestamp_precision}"
                f" --missing-labels {self.missing_labels}"
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
        """Offline re-check: rebuild both bundles' views and re-assert the guards.

        Nothing is written and nothing is fetched. The input core and match bundles
        must still hash to their own manifests; the comparison tables, the report and
        the rule self-test must be re-derivable from them under the parameters the
        delivered manifest declares.
        """
        problems: list[str] = []
        try:
            (
                core_dir,
                core_manifest,
                events,
                balances,
                match_dir,
                match_manifest,
                matches,
            ) = self.load_inputs()
        except SystemExit as exc:
            return [str(exc)]
        decision, decision_sha = self.decision()
        run_id = self.run_id(core_manifest, match_manifest, decision_sha)
        out_dir = self.cfg.out_root / run_id
        manifest_path = out_dir / "manifest.json"
        if not manifest_path.exists():
            return [f"比较产物缺失：{self.cfg.rel(manifest_path)}"]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("compare_batch_id") != run_id:
            problems.append(
                f"manifest.compare_batch_id={manifest.get('compare_batch_id')} "
                f"与输入派生的 {run_id} 不一致"
            )
        active = manifest.get("parameters", {}).get("active", {})
        for key, value in (
            ("timestamp_precision", self.timestamp_precision),
            ("missing_labels", self.missing_labels),
        ):
            if active.get(key) != value:
                problems.append(
                    f"manifest 的 {key}={active.get(key)!r} 与本次复核参数 {value!r} 不一致"
                )
        if problems:
            return problems

        # 1. both input bundles must be byte-identical to their own manifests.
        core_tables = {
            table: read_table(core_dir / f"{table}.csv")
            for table in ("events", "balances", "validation", "chain_registry")
        }
        if data_digest(core_tables) != core_manifest["digests"]["data_digest"]:
            problems.append("核心集表被改动：比较层不允许写回核心集")
        if data_digest({"matches": matches}) != match_manifest["digests"]["data_digest"]:
            problems.append("matches.csv 重算的数据摘要与匹配层 manifest 不一致")

        # 2. preconditions must still hold, and the comparison must remain eligible.
        preconditions = self.preconditions(
            events,
            balances,
            matches,
            core_manifest,
            match_manifest,
            decision,
        )
        for name, check in preconditions["checks"].items():
            if not check["passed"]:
                problems.append(f"比较前置条件未通过：{name}")

        metrics = self.metrics(events, balances, matches)
        differences = self.differences(metrics, preconditions)
        sensitivity = self.sensitivity(events, matches)
        rebuilt = {
            COMPARISON_TABLE: metrics,
            DIFFERENCE_TABLE: differences,
            SENSITIVITY_TABLE: sensitivity,
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
            problems.append("比较表重算的数据摘要与 manifest 不一致")

        report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
        recomputed = self.report(
            preconditions,
            metrics,
            differences,
            sensitivity,
            core_manifest,
            match_manifest,
            events,
            balances,
        )
        for key, value in recomputed.items():
            if report.get(key) != value:
                problems.append(f"report.json 的 {key} 与重算结果不一致")
        for caveat in REQUIRED_CAVEATS:
            if not report.get("caveats", {}).get(caveat):
                problems.append(f"report.json 缺少陈述：{caveat}")

        # 3. row-level invariants that make the table honest.
        for row in metrics:
            if row["statement_type"] != "descriptive":
                problems.append(f"{row['metric_id']}/{row['issuer']} 未标为 descriptive")
            if row["status"] not in METRIC_STATUSES:
                problems.append(f"{row['metric_id']}/{row['issuer']} 状态不在枚举内")
            if row["status"] == "reported" and row["value"] == "":
                problems.append(f"{row['metric_id']}/{row['issuer']} 报告状态却没有值")
            if row["status"] != "reported" and row["comparable"] == "yes":
                problems.append(f"{row['metric_id']}/{row['issuer']} 不可报告却标记可比")
            if row["coverage_scope"] not in {
                COVERAGE_CORE,
                COVERAGE_CLOSED_EPISODES,
                COVERAGE_RESTRICTED,
                COVERAGE_STRUCTURAL,
            }:
                problems.append(f"{row['metric_id']}/{row['issuer']} 未标注覆盖范围")
        for row in differences:
            if row["difference_status"] not in DIFFERENCE_STATUSES:
                problems.append(f"{row['metric_id']} 的差值状态不在枚举内")
            if row["coverage_scope"] not in {
                COVERAGE_CORE,
                COVERAGE_CLOSED_EPISODES,
                COVERAGE_RESTRICTED,
                COVERAGE_STRUCTURAL,
            }:
                problems.append(f"{row['metric_id']} 的差值行未标注覆盖范围")
            if row["difference_status"] == "comparable":
                expected = str(
                    Decimal(row["value_usdt"]) - Decimal(row["value_usdc"])
                )
                if Decimal(row["difference_usdt_minus_usdc"]) != Decimal(expected):
                    problems.append(f"{row['metric_id']} 的差值算术不一致")
                if row["not_comparable_reason"]:
                    problems.append(f"{row['metric_id']} 可比却带不可比原因")
            else:
                if row["difference_usdt_minus_usdc"]:
                    problems.append(f"{row['metric_id']} 不可比却给出了差值")
                if not row["not_comparable_reason"]:
                    problems.append(f"{row['metric_id']} 不可比却没有原因")
            for flag in (
                "same_observation_window",
                "same_event_definition",
                "same_missingness_treatment",
            ):
                if row[flag] != "yes":
                    problems.append(
                        f"{row['metric_id']} 的 {flag} 不为 yes：比较不应产出"
                    )
        for row in sensitivity:
            if row["conclusion_affected"] not in {"yes", "no"}:
                problems.append(f"{row['metric_id']} 的敏感性结论标记非法")
            if row["parameter"] not in {"timestamp_precision", "missing_labels"}:
                problems.append(f"{row['metric_id']} 的敏感性参数非法")
        for rule, status in recomputed["rule_checks"].items():
            if status != "ok":
                problems.append(f"规则自检失败：{rule}")

        if manifest["preconditions"] != {
            name: check["passed"] for name, check in preconditions["checks"].items()
        }:
            problems.append("manifest.preconditions 与重算结果不一致")
        if self.cfg.path(manifest["report"]["path"]) != out_dir / "report.json":
            problems.append("manifest.report.path 未指向本批次的 report.json")
        return problems
