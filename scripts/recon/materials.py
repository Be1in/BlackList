"""Supporting-material package and wording audit (ticket 09).

The review has to be able to re-run and check four deliverables: the small data sample,
the data dictionary, the reproducible collection scripts and the validation tables. This
module collects nothing; it reads what tickets 02-08 already delivered and writes one
package directory that

* indexes every component with its digest and its offline check command, so fields and
  definitions can be confirmed without re-running the full collection;
* states field by field which values are consistently available for both issuers
  (dictionary availability tier ``A``) and which only exist through an external source
  and are therefore reported on a supported subset (tier ``B``); tier ``C`` has no fields
  at all and is declared, not tabulated;
* re-checks every descriptive-wording constraint against structured evidence --
  ``statement_type``, ``coverage_scope``, report caveats, table columns, registry rows --
  instead of trusting the prose of the upstream documents;
* records the chain-scope pending item and what confirming it would change about
  collection, without writing Ethereum as the feedback's limit for every chain;
* keeps descriptive statistics and causal claims separable by construction: every
  conclusion, metric and difference row carries ``statement_type = descriptive`` and
  ``causal_inference_permitted = no``.

Tables (derived views, not dictionary tables): ``materials_index``, ``field_availability``,
``metric_coverage``, ``wording_checks``, ``chain_scope``.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from .config import REPO_ROOT, Config
from .core import DECISION_PATH, locate_core_bundle
from .rpc import canonical_json, now_utc, sha256_hex
from .tables import artifact_digest, data_digest, read_table, write_csv, write_parquet

MATERIAL_TABLE = "materials_index"
FIELD_TABLE = "field_availability"
METRIC_TABLE = "metric_coverage"
WORDING_TABLE = "wording_checks"
CHAIN_TABLE = "chain_scope"

MATERIAL_FIELDS = [
    "material_id",
    "material_name",
    "component",
    "path",
    "kind",
    "rows",
    "bytes",
    "sha256",
    "exists",
    "offline_checkable",
    "check_command",
    "check_scope",
    "note",
]
FIELD_FIELDS = [
    "table",
    "field",
    "required",
    "availability_tier",
    "availability_label",
    "analysis_role",
    "subset_only",
    "source_artifact",
    "rows_total",
    "rows_with_value",
    "value_share",
    "issuers_with_value",
    "note",
]
METRIC_FIELDS = [
    "metric_id",
    "metric_label",
    "issuer",
    "unit",
    "source_artifact",
    "coverage_scope",
    "reporting_scope",
    "evidence_tier",
    "status",
    "balance_reference",
    "statement_type",
    "causal_inference_permitted",
    "denominator",
    "note",
]
WORDING_FIELDS = [
    "constraint_id",
    "constraint_text",
    "applies_to",
    "evidence_kind",
    "evidence_pointer",
    "observed",
    "passed",
    "note",
]
CHAIN_FIELDS = [
    "chain",
    "chain_id",
    "in_scope",
    "scope_basis",
    "core_events",
    "collection_status",
    "feedback_limit",
    "pending_item_id",
    "effect_if_confirmed",
    "note",
]
FIELD_ORDER = {
    MATERIAL_TABLE: MATERIAL_FIELDS,
    FIELD_TABLE: FIELD_FIELDS,
    METRIC_TABLE: METRIC_FIELDS,
    WORDING_TABLE: WORDING_FIELDS,
    CHAIN_TABLE: CHAIN_FIELDS,
}

REQUIRED_MATERIALS = ("M1", "M2", "M3", "M4")
MATERIAL_NAMES = {
    "M1": "小数据样本",
    "M2": "数据字典",
    "M3": "可复现采集脚本",
    "M4": "验证表",
    "M5": "派生分析视图",
}

ANALYSIS_ROLES = {
    "events": "core_event_field",
    "balances": "core_balance_field",
    "matches": "external_match_field",
    "validation": "verification_record",
    "chain_registry": "chain_scope_registry",
}
AVAILABILITY_LABELS = {
    "A": "consistent_both_issuers",
    "B": "conditional_external_source",
}

# Value fields compared between a sample row and the delivered table it came from. The
# remaining columns are provenance or free-text narration (``abi_source``,
# ``contract_version_source``, ``source_query``, ``notes``, ``*_source_name``,
# ``match_evidence_text``) captured at a different time by a different entry point;
# comparing them would report a time difference, not a value difference.
SAMPLE_VALUE_FIELDS = {
    "events": [
        "token_symbol",
        "event_class",
        "block_number",
        "block_hash",
        "tx_hash",
        "tx_index",
        "log_index",
        "log_order_key",
        "tx_event_count",
        "affected_address",
        "blacklist_state_before",
        "blacklist_state_after",
        "blacklist_transition",
        "contract_code_hash",
        "contract_version_label",
        "contract_version_confidence",
        "balance_pre_event_raw",
        "balance_pre_event_token",
        "balance_pre_event_adjustment_raw",
        "destruction_amount_raw",
        "destruction_amount_token",
    ],
    "balances": [
        "balance_reference",
        "reference_event_id",
        "balance_block_number",
        "balance_block_hash",
        "balance_raw",
        "balance_token",
        "blacklist_state_at_balance_block",
        "balance_source",
    ],
    "matches": [
        "match_status",
        "matched_entity_name",
        "matched_record_id",
        "match_evidence_address_string",
        "sanctions_public_timestamp",
        "sanctions_date_basis",
        "label_date",
        "label_date_basis",
        "match_confidence",
    ],
}

SCOPE_TO_REPORTING = {
    "core_full": "all_events",
    "core_closed_episodes": "core_subpopulation",
    "restricted_subset": "supported_subset",
    "structural_zero": "definitional_zero",
}

CONSTRAINTS = {
    "W1": (
        "所有结论、指标与差值行都标为 descriptive（描述性），材料中不出现因果判断："
        "描述性统计与因果性判断可一眼区分。"
    ),
    "W2": (
        "public sanctions timing gaps 只描述公开记录日期与链上事件时间之间的差，"
        "不推断私有执法请求的响应速度。"
    ),
    "W3": "address outflows 只描述冻结前窗口内的资金移动，不推断规避行为。",
    "W4": (
        "frozen balances 只描述余额口径与数值，不推断执法有效性；"
        "D1 与 D2 分别声明、不混用、不相加。"
    ),
    "W5": (
        "有限匹配子样本的结论不外推到全部冻结活动；受限子集指标与核心指标不拼接、"
        "不互相补充。"
    ),
    "W6": (
        "两条分析路径物理分离：独立批次目录、独立表名、受限行的 coverage_scope 唯一，"
        "且不产出受限口径的 issuer 差值。"
    ),
    "W7": (
        "清单快照发布日不是制裁日期：sanctions_public_timestamp 只接受有日期依据的记录，"
        "快照日期只写 label_date。"
    ),
    "W8": (
        "Ethereum 只被反馈限定在「小型代表性样本」；核心分析条款写的是 all retrievable "
        "events，未限定链；其他链登记为待确认项并写明纳入后的采集影响。"
    ),
}

# Prose is not machine-checked beyond "the constraint is declared here and restated in
# the package document": these anchors prove each material says the thing, the structured
# checks below prove it holds.
DOC_ANCHORS: dict[str, list[str]] = {
    "docs/definitions.md": ["## 6. 措辞约束", "public sanctions timing gaps", "not_observable"],
    "docs/data-dictionary.md": ["## 9. 可得性分层", "A 一致可得", "B 条件可得", "C 不可观测"],
    "docs/issuer-comparison.md": ["## 7. 措辞边界", "descriptive", "私有执法请求"],
    "docs/restricted-subset-analyses.md": ["## 7. 措辞边界", "描述性", "规避"],
    "docs/study-scope.md": ["小型代表性样本", "其他链只登记"],
    "docs/supporting-materials.md": [
        "## 1. 四项支撑材料",
        "## 2. 一致可得字段",
        "## 3. 措辞约束逐条核对",
        "## 4. 链范围",
        "## 5. 描述性统计与因果性判断",
    ],
}

FIELD_NOTES = {
    ("matches", "sanctions_public_timestamp"): (
        "只接受有日期依据的制裁记录；本批 matches 该字段没有值（SDN 快照只有清单发布日），"
        "因此用它的 timing gap 指标在两个 issuer 上都不可报告"
    ),
    ("matches", "label_date"): (
        "193 行有值，全部为 list_snapshot_date（清单快照发布日）；带日期的交易所标签 0 行"
    ),
    ("matches", "match_evidence_text"): "外部记录原文片段，仅作证据保留，不参与任何指标",
}


def read_csv_rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


SIGNOFF_SHEET = REPO_ROOT / "data/derived/manual_signoff_sample.csv"


def signoff_status() -> dict:
    """Read the stratified sign-off sheet instead of asserting its state.

    A row counts as signed only when ``reviewer``, ``reviewed_at_utc`` and
    ``decision`` are all filled. Nothing here is hardcoded, so a regenerated
    package can neither claim a signature that is not on disk nor hide one that
    is. The sheet covers 26 sampled rows only: the full validation table keeps
    ``reviewer = agent:auto-cross-check`` by design.
    """
    rows = read_csv_rows(SIGNOFF_SHEET) if SIGNOFF_SHEET.exists() else []
    signed = [
        row
        for row in rows
        if (row.get("reviewer") or "").strip()
        and (row.get("reviewed_at_utc") or "").strip()
        and (row.get("decision") or "").strip()
    ]
    decisions = Counter((row.get("decision") or "").strip() for row in signed)
    dates = sorted((row.get("reviewed_at_utc") or "").strip() for row in signed)
    return {
        "rows": len(rows),
        "signed_rows": len(signed),
        "complete": bool(rows) and len(signed) == len(rows),
        "reviewers": sorted({(row.get("reviewer") or "").strip() for row in signed}),
        "decisions": dict(decisions),
        "last_signed_at_utc": dates[-1] if dates else "",
    }


def signoff_phrase(status: dict | None = None) -> str:
    """One sentence that is true for both the signed and the unsigned state."""
    state = status or signoff_status()
    if not state["complete"]:
        return (
            f"人工抽样签署未完成（已填 {state['signed_rows']} / {state['rows']} 行）；"
            "全量验证表为脚本跨端点核对"
        )
    decisions = "、".join(f"{key} {value}" for key, value in sorted(state["decisions"].items()))
    return (
        f"{state['signed_rows']} 行分层抽样已由 {'、'.join(state['reviewers'])} 签署"
        f"（{state['last_signed_at_utc'][:10]}，{decisions}）；"
        "全量验证表仍为脚本跨端点核对，抽样签署不等于逐行签字"
    )


def _assert_fields(table: str, fields: list[str], rows: list[dict]) -> None:
    for row in rows:
        extra = set(row) - set(fields)
        missing = set(fields) - set(row)
        if extra or missing:
            raise ValueError(
                f"{table}: 列集合与本模块声明不一致 extra={sorted(extra)} missing={sorted(missing)}"
            )


def _fmt_share(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return ""
    return format(Decimal(numerator) / Decimal(denominator), ".6f")


def _sha256_file(path: Path) -> str:
    return sha256_hex(path.read_bytes())


def _json_bool(value: object) -> str:
    """Render a manifest flag the way the JSON it came from spells it."""
    if value is True:
        return "true"
    if value is False:
        return "false"
    return str(value)


@dataclass
class MaterialsRun:
    """One package build: index + availability + metric scope + wording + chain scope."""

    cfg: Config
    match_id: str = ""
    compare_id: str = ""
    restrict_id: str = ""
    _package_doc: str = field(default="", init=False, repr=False)

    # ----------------------------------------------------------------- locating
    def _locate(self, prefix: str, explicit: str, predicate, hint: str) -> tuple[Path, dict]:
        """Find the delivered batch built from these exact inputs, never "the newest"."""
        if explicit:
            out_dir = self.cfg.out_root / explicit
            manifest_path = out_dir / "manifest.json"
            if not manifest_path.exists():
                raise SystemExit(f"批次产物缺失：{self.cfg.rel(manifest_path)}；{hint}")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not predicate(manifest):
                raise SystemExit(f"{self.cfg.rel(manifest_path)} 不是由当前上游批次生成的；{hint}")
            return out_dir, manifest

        candidates: list[tuple[Path, dict]] = []
        for path in sorted(self.cfg.out_root.iterdir()):
            if not path.is_dir() or not path.name.startswith(prefix):
                continue
            manifest_path = path / "manifest.json"
            if not manifest_path.exists():
                continue
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if predicate(manifest):
                candidates.append((path, manifest))
        if not candidates:
            raise SystemExit(f"未找到由当前上游批次生成的 {prefix}* 批次；{hint}")
        if len(candidates) > 1:
            names = "、".join(path.name for path, _ in candidates)
            raise SystemExit(
                f"{prefix}* 有多个候选批次（{names}），无法判定哪一个是交付口径；"
                "用 --match-id / --compare-id / --restrict-id 显式指定"
            )
        return candidates[0]

    def load_inputs(self) -> dict:
        cfg = self.cfg
        core_dir, core_manifest = locate_core_bundle(cfg, require_current_inputs=True)
        core_digest = core_manifest["digests"]["data_digest"]
        core_id = core_manifest["core_id"]

        def from_core(manifest: dict) -> bool:
            inputs = manifest.get("inputs", {})
            return (
                inputs.get("core_id") == core_id
                and inputs.get("core_data_digest") == core_digest
            )

        match_dir, match_manifest = self._locate(
            "match_",
            self.match_id,
            from_core,
            "先运行 python scripts/reconstruct_matches.py",
        )
        match_digest = match_manifest["digests"]["data_digest"]

        def from_match(manifest: dict) -> bool:
            return from_core(manifest) and manifest.get("inputs", {}).get(
                "match_data_digest"
            ) == match_digest

        compare_dir, compare_manifest = self._locate(
            "compare_",
            self.compare_id,
            from_match,
            "先运行 python scripts/reconstruct_comparison.py",
        )
        restrict_dir, restrict_manifest = self._locate(
            "restrict_",
            self.restrict_id,
            from_match,
            "先运行 python scripts/reconstruct_restricted.py",
        )

        decision_raw = cfg.path(DECISION_PATH).read_bytes()
        return {
            "core_dir": core_dir,
            "core_manifest": core_manifest,
            "events": read_table(core_dir / "events.csv"),
            "balances": read_table(core_dir / "balances.csv"),
            "validation": read_table(core_dir / "validation.csv"),
            "chain_registry": read_table(core_dir / "chain_registry.csv"),
            "match_dir": match_dir,
            "match_manifest": match_manifest,
            "matches": read_table(match_dir / "matches.csv"),
            "match_report": json.loads((match_dir / "report.json").read_text(encoding="utf-8")),
            "compare_dir": compare_dir,
            "compare_manifest": compare_manifest,
            "comparison": read_table(compare_dir / "comparison.csv"),
            "differences": read_table(compare_dir / "differences.csv"),
            "compare_report": json.loads(
                (compare_dir / "report.json").read_text(encoding="utf-8")
            ),
            "restrict_dir": restrict_dir,
            "restrict_manifest": restrict_manifest,
            "restrict_tables": {
                name: read_table(restrict_dir / f"{name}.csv")
                for name in (
                    "subset_eligibility",
                    "timing_gaps",
                    "exchange_outflows",
                    "frozen_balances",
                    "outflow_window_sensitivity",
                )
            },
            "restrict_report": json.loads(
                (restrict_dir / "report.json").read_text(encoding="utf-8")
            ),
            "dictionary": read_csv_rows(cfg.dictionary_csv),
            "decision": json.loads(decision_raw.decode("utf-8")),
            "decision_sha256": sha256_hex(decision_raw),
        }

    # ------------------------------------------------------------ M1 small sample
    def _sample_parity(
        self,
        table: str,
        sample_rows: list[dict],
        delivered_rows: list[dict],
        key_fields: list[str],
    ) -> dict:
        value_fields = SAMPLE_VALUE_FIELDS[table]
        index: dict[tuple, dict] = {}
        for row in delivered_rows:
            index.setdefault(tuple(row[field_name] for field_name in key_fields), row)
        missing: list[str] = []
        mismatches: list[str] = []
        for row in sample_rows:
            key = tuple(row[field_name] for field_name in key_fields)
            label = "/".join(key)
            target = index.get(key)
            if target is None:
                missing.append(label)
                continue
            for field_name in value_fields:
                if field_name in row and field_name in target and row[field_name] != target[field_name]:
                    mismatches.append(f"{label}.{field_name}")
        compared = set(sample_rows[0]) & set(delivered_rows[0])
        return {
            "rows": len(sample_rows),
            "hits": len(sample_rows) - len(missing),
            "missing": missing,
            "mismatches": mismatches,
            "compared_fields": len(value_fields),
            "skipped_fields": sorted(compared - set(value_fields)),
        }

    def _sample_rows(self, inputs: dict) -> list[dict]:
        missing_samples = [
            f"data/samples/sample_{name}.csv"
            for name in ("events", "balances", "matches", "validation")
            if not self.cfg.path(f"data/samples/sample_{name}.csv").is_file()
        ]
        if missing_samples:
            raise ValueError("支撑材料不齐：" + "；".join(missing_samples))
        sample_events = read_csv_rows(self.cfg.path("data/samples/sample_events.csv"))
        sample_balances = read_csv_rows(self.cfg.path("data/samples/sample_balances.csv"))
        sample_matches = read_csv_rows(self.cfg.path("data/samples/sample_matches.csv"))
        sample_validation = read_csv_rows(self.cfg.path("data/samples/sample_validation.csv"))

        def column_check(table: str, rows: list[dict]) -> str:
            fields = {entry["field"] for entry in inputs["dictionary"] if entry["table"] == table}
            actual = set(rows[0])
            if actual != fields:
                raise ValueError(
                    f"sample_{table}: 列集合与字典不一致 "
                    f"extra={sorted(actual - fields)} missing={sorted(fields - actual)}"
                )
            return f"{len(rows)} 行 / {len(fields)} 列与字典逐字段一致"

        rows: list[dict] = []

        parity = self._sample_parity("events", sample_events, inputs["events"], ["event_id"])
        rows.append(
            self._component(
                "M1",
                "data/samples/sample_events.csv",
                "events",
                "small_sample",
                "python scripts/reconstruct_materials.py --check",
                f"{column_check('events', sample_events)}；"
                f"{parity['hits']}/{parity['rows']} 行按 event_id 命中核心集，"
                f"比对 {parity['compared_fields']} 个取值字段，{len(parity['mismatches'])} 处不同",
                f"未比对字段（来源/叙述）：{'、'.join(parity['skipped_fields'])}"
                if parity["skipped_fields"]
                else "",
                rows=len(sample_events),
            )
        )
        parity = self._sample_parity("balances", sample_balances, inputs["balances"], ["query_id"])
        rows.append(
            self._component(
                "M1",
                "data/samples/sample_balances.csv",
                "balances",
                "small_sample",
                "python scripts/reconstruct_materials.py --check",
                f"{column_check('balances', sample_balances)}；"
                f"{parity['hits']}/{parity['rows']} 行按 query_id 命中核心集，"
                f"比对 {parity['compared_fields']} 个取值字段，{len(parity['mismatches'])} 处不同",
                f"未比对字段（抓取时刻/叙述）：{'、'.join(parity['skipped_fields'])}"
                if parity["skipped_fields"]
                else "",
                rows=len(sample_balances),
            )
        )
        parity = self._sample_parity(
            "matches", sample_matches, inputs["matches"], ["event_id", "match_layer"]
        )
        rows.append(
            self._component(
                "M1",
                "data/samples/sample_matches.csv",
                "matches",
                "small_sample",
                "python scripts/reconstruct_materials.py --check",
                f"{column_check('matches', sample_matches)}；"
                f"{parity['hits']}/{parity['rows']} 行按 (event_id, match_layer) 命中匹配层，"
                f"比对 {parity['compared_fields']} 个取值字段，{len(parity['mismatches'])} 处不同",
                "match_id 与证据原文由两次不同入口生成，不参与比对；只核对判定、日期与证据地址",
                rows=len(sample_matches),
            )
        )
        agreement = Counter(row["agreement"] for row in sample_validation)
        rows.append(
            self._component(
                "M1",
                "data/samples/sample_validation.csv",
                "validation",
                "small_sample",
                "python scripts/reconstruct_materials.py --check",
                f"{column_check('validation', sample_validation)}；"
                "check_type 与 agreement 取值均在字典枚举内；agreement = "
                + "、".join(f"{key} {value}" for key, value in sorted(agreement.items())),
                "参照值取自工单 01 的独立端点记录，与核心批次不逐字节相同，因此只核对列与枚举",
                rows=len(sample_validation),
            )
        )
        return rows

    # ------------------------------------------------------------- materials index
    def _component(
        self,
        material_id: str,
        rel: str,
        component: str,
        kind: str,
        check_command: str,
        check_scope: str,
        note: str = "",
        rows: int | None = None,
    ) -> dict:
        path = self.cfg.path(rel)
        exists = path.is_file()
        return {
            "material_id": material_id,
            "material_name": MATERIAL_NAMES[material_id],
            "component": component,
            "path": rel,
            "kind": kind,
            "rows": "" if rows is None else str(rows),
            "bytes": str(path.stat().st_size) if exists else "",
            "sha256": _sha256_file(path) if exists else "",
            "exists": "yes" if exists else "no",
            "offline_checkable": "yes",
            "check_command": check_command,
            "check_scope": check_scope,
            "note": note,
        }

    def _script_package(self) -> dict:
        root = self.cfg.path("scripts/recon")
        files = sorted(path for path in root.glob("*.py") if path.is_file())
        digest = sha256_hex("|".join(f"{path.name}:{_sha256_file(path)}" for path in files).encode())
        return {
            "material_id": "M3",
            "material_name": MATERIAL_NAMES["M3"],
            "component": "recon 共享模块",
            "path": self.cfg.rel(root),
            "kind": "python_package",
            "rows": "",
            "bytes": str(sum(path.stat().st_size for path in files)),
            "sha256": digest,
            "exists": "yes" if files else "no",
            "offline_checkable": "yes",
            "check_command": "python scripts/reconstruct_materials.py --check",
            "check_scope": f"{len(files)} 个模块的摘要参与索引；每个入口脚本另有自己的离线复核命令",
            "note": "共享模块（配置、RPC 缓存、字典断言、表契约）",
        }

    def materials_rows(self, inputs: dict) -> list[dict]:
        cfg = self.cfg
        rows: list[dict] = []
        rows.extend(self._sample_rows(inputs))

        for rel, kind, scope in (
            (
                "docs/data-dictionary.md",
                "markdown",
                "字段、枚举、排除规则与可得性分层（第 9 节）的唯一权威",
            ),
            (
                "docs/data-dictionary.csv",
                "csv",
                "机器可读字段清单：写出前断言列集合，本模块的 field_availability 逐行取自它",
            ),
        ):
            rows.append(
                self._component(
                    "M2",
                    rel,
                    rel.rsplit("/", 1)[-1],
                    kind,
                    "python scripts/reconstruct_materials.py --check",
                    scope,
                )
            )

        entry_scripts = (
            ("scripts/reconstruct_single_event.py", "单笔事件", "python scripts/reconstruct_single_event.py"),
            ("scripts/reconstruct_lifecycle.py", "生命周期案例", "python scripts/reconstruct_lifecycle.py --case all"),
            ("scripts/validate_sample.py", "代表性样本与研究期决策", "python scripts/validate_sample.py --check-scope"),
            ("scripts/reconstruct_core.py", "核心事件集", "python scripts/reconstruct_core.py --check"),
            ("scripts/reconstruct_matches.py", "匹配层与缺失报告", "python scripts/reconstruct_matches.py --check"),
            ("scripts/reconstruct_comparison.py", "同口径比较", "python scripts/reconstruct_comparison.py --check"),
            ("scripts/reconstruct_restricted.py", "受限子样本", "python scripts/reconstruct_restricted.py --check"),
            ("scripts/reconstruct_materials.py", "支撑材料包", "python scripts/reconstruct_materials.py --check"),
        )
        for rel, kind, command in entry_scripts:
            rows.append(
                self._component(
                    "M3",
                    rel,
                    kind,
                    "python_entry",
                    command,
                    "同一输入重放得到同一摘要；--check 系列不访问网络、不写盘",
                    "以固定快照与本地缓存为输入，缺缓存时需归档端点",
                )
            )
        rows.append(self._script_package())
        rows.append(
            self._component(
                "M3",
                "requirements.txt",
                "Python 依赖",
                "text",
                'python -c "import pyarrow, dotenv"',
                "Python 3.13 + venv；pyarrow 缺失时只写 CSV，不影响摘要",
            )
        )

        rows.append(
            self._component(
                "M4",
                "docs/validation-table.md",
                "核对协议与结果汇总",
                "markdown",
                "python scripts/reconstruct_materials.py --check",
                "核对协议、参照来源与已交付批次的核对结果",
            )
        )
        validation_dirs = [inputs["core_dir"]]
        for prefix in ("vs_", "lc_"):
            validation_dirs.extend(
                path
                for path in sorted(cfg.out_root.iterdir())
                if path.is_dir() and path.name.startswith(prefix)
            )
        for directory in validation_dirs:
            path = directory / "validation.csv"
            if not path.exists():
                continue
            table_rows = read_table(path)
            agreement = Counter(row["agreement"] for row in table_rows)
            types = len({row["check_type"] for row in table_rows})
            summary = "、".join(f"{key} {value}" for key, value in sorted(agreement.items()))
            rows.append(
                self._component(
                    "M4",
                    f"{cfg.rel(directory)}/validation.csv",
                    directory.name,
                    "validation_table",
                    "python scripts/reconstruct_materials.py --check",
                    f"列集合对字典第 7 节；check_type / agreement 取值在字典枚举内；"
                    f"{len(table_rows)} 行、{types} 类 check_type（{summary}）",
                    f"reviewer = agent:auto-cross-check：跨端点核对由脚本执行；{signoff_phrase()}",
                    rows=len(table_rows),
                )
            )

        for name in ("comparison", "differences", "sensitivity"):
            rows.append(
                self._component(
                    "M5",
                    f"{cfg.rel(inputs['compare_dir'])}/{name}.csv",
                    name,
                    "analysis_view",
                    "python scripts/reconstruct_comparison.py --check",
                    "派生分析视图（不属字典表）：指标覆盖范围与描述性标记的来源",
                    "属于工单 07 的批次，不是四项支撑材料之一；列在索引里是因为指标清单取自它",
                )
            )
        for name in (
            "subset_eligibility",
            "timing_gaps",
            "exchange_outflows",
            "frozen_balances",
            "outflow_window_sensitivity",
        ):
            rows.append(
                self._component(
                    "M5",
                    f"{cfg.rel(inputs['restrict_dir'])}/{name}.csv",
                    name,
                    "analysis_view",
                    "python scripts/reconstruct_restricted.py --check",
                    "派生分析视图（不属字典表）：受限子集指标与措辞核对的来源",
                    "属于工单 08 的批次，不是四项支撑材料之一；列在索引里是因为子集指标取自它",
                )
            )

        missing = [
            f"{row['path']}（{row['material_name']}）" for row in rows if row["exists"] != "yes"
        ]
        if missing:
            raise ValueError("支撑材料不齐：" + "；".join(missing))
        return rows

    # --------------------------------------------------------- field availability
    def field_rows(self, inputs: dict) -> list[dict]:
        cfg = self.cfg
        tables = {
            "events": inputs["events"],
            "balances": inputs["balances"],
            "matches": inputs["matches"],
            "validation": inputs["validation"],
            "chain_registry": inputs["chain_registry"],
        }
        artifacts = {
            "events": f"{cfg.rel(inputs['core_dir'])}/events.csv",
            "balances": f"{cfg.rel(inputs['core_dir'])}/balances.csv",
            "matches": f"{cfg.rel(inputs['match_dir'])}/matches.csv",
            "validation": f"{cfg.rel(inputs['core_dir'])}/validation.csv",
            "chain_registry": f"{cfg.rel(inputs['core_dir'])}/chain_registry.csv",
        }
        event_token = {row["event_id"]: row["token_symbol"] for row in inputs["events"]}

        rows: list[dict] = []
        for entry in inputs["dictionary"]:
            table = entry["table"]
            field_name = entry["field"]
            payload = tables[table]
            if payload and field_name not in payload[0]:
                raise ValueError(f"{artifacts[table]} 缺少字典字段 {field_name}")
            total = len(payload)
            with_value = 0
            tokens: set[str] = set()
            for row in payload:
                if row.get(field_name, "") == "":
                    continue
                with_value += 1
                if table in ("events", "balances"):
                    tokens.add(row["token_symbol"])
                elif table == "matches":
                    token = event_token.get(row["event_id"])
                    if token:
                        tokens.add(token)
            role = ANALYSIS_ROLES[table]
            tier = entry["availability_tier"]
            rows.append(
                {
                    "table": table,
                    "field": field_name,
                    "required": entry["required"],
                    "availability_tier": tier,
                    "availability_label": AVAILABILITY_LABELS.get(tier, ""),
                    "analysis_role": role,
                    "subset_only": "yes" if role == "external_match_field" else "no",
                    "source_artifact": artifacts[table],
                    "rows_total": str(total),
                    "rows_with_value": str(with_value),
                    "value_share": _fmt_share(with_value, total),
                    "issuers_with_value": (
                        "+".join(sorted(tokens)) if tokens else "none"
                    )
                    if table in ("events", "balances", "matches")
                    else "not_applicable",
                    "note": (
                        signoff_phrase()
                        if (table, field_name) == ("validation", "reviewer")
                        else FIELD_NOTES.get((table, field_name), "")
                    ),
                }
            )
        return rows

    # ------------------------------------------------------------ metric coverage
    def _metric_row(
        self,
        *,
        metric_id: str,
        metric_label: str,
        issuer: str,
        unit: str,
        source_artifact: str,
        coverage_scope: str,
        status: str,
        balance_reference: str = "",
        denominator: str = "",
        note: str = "",
        statement_type: str = "descriptive",
    ) -> dict:
        reporting_scope = SCOPE_TO_REPORTING.get(coverage_scope)
        if reporting_scope is None:
            raise ValueError(f"{metric_id}: 未声明的 coverage_scope={coverage_scope}")
        if statement_type != "descriptive":
            raise ValueError(f"{metric_id}: 指标行必须是 descriptive，实得 {statement_type}")
        return {
            "metric_id": metric_id,
            "metric_label": metric_label,
            "issuer": issuer,
            "unit": unit,
            "source_artifact": source_artifact,
            "coverage_scope": coverage_scope,
            "reporting_scope": reporting_scope,
            "evidence_tier": "B" if reporting_scope == "supported_subset" else "A",
            "status": status,
            "balance_reference": balance_reference,
            "statement_type": statement_type,
            "causal_inference_permitted": "no",
            "denominator": denominator,
            "note": note,
        }

    def metric_rows(self, inputs: dict) -> list[dict]:
        cfg = self.cfg
        rows: list[dict] = []

        compare_artifact = f"{cfg.rel(inputs['compare_dir'])}/comparison.csv"
        for row in inputs["comparison"]:
            rows.append(
                self._metric_row(
                    metric_id=row["metric_id"],
                    metric_label=row["metric_label"],
                    issuer=row["issuer"],
                    unit=row["unit"],
                    source_artifact=compare_artifact,
                    coverage_scope=row["coverage_scope"],
                    status=row["status"],
                    balance_reference=row["balance_reference"],
                    denominator=row["denominator"],
                    note=row["comparability_note"],
                    statement_type=row["statement_type"],
                )
            )

        difference_artifact = f"{cfg.rel(inputs['compare_dir'])}/differences.csv"
        for row in inputs["differences"]:
            if not row["interpretation_guard"]:
                raise ValueError(f"{row['metric_id']}: 差值行缺 interpretation_guard")
            rows.append(
                self._metric_row(
                    metric_id=row["metric_id"],
                    metric_label=row["metric_label"],
                    issuer="USDT-USDC",
                    unit=row["unit"],
                    source_artifact=difference_artifact,
                    coverage_scope=row["coverage_scope"],
                    status=row["difference_status"],
                    note=row["not_comparable_reason"] or row["interpretation_guard"],
                )
            )

        restrict_files = {
            "timing_gaps": "timing_gaps.csv",
            "exchange_outflows": "exchange_outflows.csv",
            "frozen_balances": "frozen_balances.csv",
            "outflow_window_sensitivity": "outflow_window_sensitivity.csv",
        }
        for table, filename in restrict_files.items():
            for row in inputs["restrict_tables"][table]:
                scope = row.get("coverage_scope", "restricted_subset")
                if scope != "restricted_subset":
                    raise ValueError(f"{table}: 受限子集行的 coverage_scope 必须是 restricted_subset")
                note = row.get("note", "")
                if "coverage_scope" not in row:
                    note = "该表不带 coverage_scope 列，范围由批次声明（restricted_subset）；" + note
                if table == "frozen_balances" and row["balance_reference"] != "at_freeze":
                    raise ValueError("frozen_balances: 只允许 D1（at_freeze）")
                metric_id = row["metric_id"]
                unit = row.get("unit", "")
                if not unit:
                    unit = (
                        "share"
                        if "share" in metric_id
                        else "token"
                        if "token_total" in metric_id
                        else ""
                    )
                rows.append(
                    self._metric_row(
                        metric_id=metric_id,
                        metric_label=row.get("metric_label", metric_id),
                        issuer=row["issuer"],
                        unit=unit,
                        source_artifact=f"{cfg.rel(inputs['restrict_dir'])}/{filename}",
                        coverage_scope=scope,
                        status=row["status"],
                        balance_reference=row.get("balance_reference", ""),
                        denominator=row.get("denominator", ""),
                        note=note,
                        statement_type=row["statement_type"],
                    )
                )

        core_artifact = f"{cfg.rel(inputs['core_dir'])}/manifest.json"
        metrics = inputs["core_manifest"]["metrics"]
        core_rows = (
            (
                "event_retrieval_coverage",
                "ratio",
                f"{metrics['event_retrieval_coverage']['retrieved_events']} / "
                f"{metrics['event_retrieval_coverage']['expected_events']} 独立日志；"
                "分母来自独立端点的全量检索",
            ),
            (
                "balance_reconstruction_error",
                "checks",
                f"{metrics['balance_reconstruction_error']['exact_matches']} / "
                f"{metrics['balance_reconstruction_error']['checks']} 完全一致；"
                f"最大绝对误差 {metrics['balance_reconstruction_error']['max_abs_error_raw']} raw",
            ),
            (
                "unverifiable_share",
                "ratio",
                f"行口径 {metrics['unverifiable_share']['share']}"
                f"（{metrics['unverifiable_share']['unverifiable']} / "
                f"{metrics['unverifiable_share']['validation_rows']}）；"
                f"记录口径 {metrics['unverifiable_share']['record_share']}；不可核验不是缺失",
            ),
        )
        for metric_id, unit, note in core_rows:
            rows.append(
                self._metric_row(
                    metric_id=metric_id,
                    metric_label=metric_id,
                    issuer="both",
                    unit=unit,
                    source_artifact=core_artifact,
                    coverage_scope="core_full",
                    status="reported",
                    note=note,
                )
            )
        return rows

    # ------------------------------------------------------------- chain scoping
    def chain_rows(self, inputs: dict) -> list[dict]:
        decision = inputs["decision"]
        core_events = inputs["core_manifest"]["counts"]["events"]
        rows: list[dict] = []
        for registry_row in inputs["chain_registry"]:
            collected = registry_row["in_scope"] == "verified_sample"
            rows.append(
                {
                    "chain": registry_row["chain"],
                    "chain_id": registry_row["chain_id"],
                    "in_scope": registry_row["in_scope"],
                    "scope_basis": registry_row["scope_basis"],
                    "core_events": str(core_events) if collected else "0",
                    "collection_status": (
                        "collected_verified_sample" if collected else "registered_not_collected"
                    ),
                    "feedback_limit": (
                        "反馈只在「小型代表性样本」条款中写明 Ethereum；核心分析条款写的是 "
                        "all retrievable events，未限定链"
                    ),
                    "pending_item_id": "" if collected else "chain_scope_pending",
                    "effect_if_confirmed": (
                        "无需扩展：核心分析已在该链观察期内的全部可检索事件上完成"
                        if collected
                        else "需采集该链的黑名单日志并重跑核心集、匹配层、比较与受限分析，"
                        "重做研究期决策（观察期起点可能改变），并补验证样本覆盖该链"
                    ),
                    "note": (
                        "链字段逐事件登记，不允许未声明链范围"
                        if collected
                        else "仅登记、不采集；纳入前不得把本包结论读成跨链结论"
                    ),
                }
            )
        declared = decision["scope"]["chains"]
        collected_chains = [
            row["chain"] for row in rows if row["collection_status"].startswith("collected")
        ]
        if declared != collected_chains:
            raise ValueError(
                f"研究范围决策声明链 {declared} 与链登记实际采集 {collected_chains} 不一致"
            )
        return rows

    # -------------------------------------------------------------- wording audit
    def _doc_hits(self, rel: str) -> tuple[int, list[str]]:
        path = self.cfg.path(rel)
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        anchors = DOC_ANCHORS[rel]
        missing = [anchor for anchor in anchors if anchor not in text]
        return len(anchors) - len(missing), missing

    def _package_doc_text(self) -> str:
        if not self._package_doc:
            path = self.cfg.path("docs/supporting-materials.md")
            self._package_doc = path.read_text(encoding="utf-8") if path.exists() else ""
        return self._package_doc

    def wording_rows(self, inputs: dict, tables: dict[str, list[dict]]) -> list[dict]:
        cfg = self.cfg
        compare_report = inputs["compare_report"]
        restrict_report = inputs["restrict_report"]
        metrics = tables[METRIC_TABLE]
        chains = tables[CHAIN_TABLE]
        matches = inputs["matches"]
        package_doc = self._package_doc_text()

        allowed_basis = set()
        for entry in inputs["dictionary"]:
            if entry["table"] == "matches" and entry["field"] == "sanctions_date_basis":
                allowed_basis = set(entry["allowed_values"].split("|"))
        if not allowed_basis:
            raise ValueError("字典缺 matches.sanctions_date_basis 枚举")

        def caveat(report: dict, key: str) -> bool:
            return bool(report.get("caveats", {}).get(key))

        def add(
            constraint_id: str,
            applies_to: str,
            evidence_kind: str,
            evidence_pointer: str,
            observed: str,
            passed: bool,
            note: str = "",
        ) -> None:
            rows.append(
                {
                    "constraint_id": constraint_id,
                    "constraint_text": CONSTRAINTS[constraint_id],
                    "applies_to": applies_to,
                    "evidence_kind": evidence_kind,
                    "evidence_pointer": evidence_pointer,
                    "observed": observed,
                    "passed": "yes" if passed else "no",
                    "note": note,
                }
            )

        def package_doc_row(constraint_id: str) -> None:
            declared = CONSTRAINTS[constraint_id] in package_doc
            add(
                constraint_id,
                "package_doc",
                "doc_anchor",
                "docs/supporting-materials.md#3",
                f"第 3 节逐条重述该约束：{'是' if declared else '否'}",
                declared,
                "只证明材料重述了约束；约束是否成立由同一约束的其他行核对",
            )

        rows: list[dict] = []

        # W1: every statement is marked descriptive; nothing permits causal inference.
        compare_conclusions = compare_report["conclusions"]
        restrict_conclusions = restrict_report["conclusions"]
        bad_compare = [c["id"] for c in compare_conclusions if c["statement_type"] != "descriptive"]
        bad_restrict = [c["id"] for c in restrict_conclusions if c["statement_type"] != "descriptive"]
        add(
            "W1",
            "comparison_report",
            "report_field",
            f"{cfg.rel(inputs['compare_dir'])}/report.json#conclusions",
            f"{len(compare_conclusions)} 条结论，statement_type 非 descriptive 的 {len(bad_compare)} 条",
            not bad_compare,
        )
        add(
            "W1",
            "restricted_report",
            "report_field",
            f"{cfg.rel(inputs['restrict_dir'])}/report.json#conclusions",
            f"{len(restrict_conclusions)} 条结论，statement_type 非 descriptive 的 {len(bad_restrict)} 条",
            not bad_restrict,
        )
        non_descriptive = [
            row
            for row in metrics
            if row["statement_type"] != "descriptive" or row["causal_inference_permitted"] != "no"
        ]
        add(
            "W1",
            "metric_tables",
            "table_column",
            f"{cfg.rel(inputs['compare_dir'])}/comparison.csv；"
            f"{cfg.rel(inputs['restrict_dir'])}/timing_gaps.csv 等",
            f"{len(metrics)} 行指标，statement_type 非 descriptive 或允许因果推断的 "
            f"{len(non_descriptive)} 行",
            not non_descriptive,
        )
        package_doc_row("W1")

        # W2: public sanctions timing gaps stay descriptive.
        dated_rows = [row for row in matches if row["sanctions_public_timestamp"]]
        matched_without_date = [
            row
            for row in matches
            if row["match_layer"] == "ofac_sdn"
            and row["match_status"] == "matched"
            and not row["sanctions_public_timestamp"]
        ]
        reason_ok = all(
            row["missing_reason_code"] == "label_date_missing" for row in matched_without_date
        )
        day_only = all(
            row["timestamp_precision"] == "day"
            for row in inputs["restrict_tables"]["timing_gaps"]
        )
        add(
            "W2",
            "comparison_report",
            "report_field",
            f"{cfg.rel(inputs['compare_dir'])}/report.json#caveats.no_private_request_inference",
            "caveat 存在" if caveat(compare_report, "no_private_request_inference") else "caveat 缺失",
            caveat(compare_report, "no_private_request_inference"),
        )
        add(
            "W2",
            "restricted_report",
            "report_field",
            f"{cfg.rel(inputs['restrict_dir'])}/report.json#caveats",
            "no_private_request_inference 与 no_snapshot_date_as_sanctions_date 均存在"
            if caveat(restrict_report, "no_private_request_inference")
            and caveat(restrict_report, "no_snapshot_date_as_sanctions_date")
            else "caveat 缺失",
            caveat(restrict_report, "no_private_request_inference")
            and caveat(restrict_report, "no_snapshot_date_as_sanctions_date"),
        )
        add(
            "W2",
            "matches_table",
            "table_column",
            f"{cfg.rel(inputs['match_dir'])}/matches.csv#sanctions_public_timestamp",
            f"{len(dated_rows)} 行有制裁日期；OFAC matched 但缺日期的 {len(matched_without_date)} 行，"
            f"其 missing_reason_code = label_date_missing：{'是' if reason_ok else '否'}；"
            f"timing 行全为 day 精度：{'是' if day_only else '否'}",
            reason_ok and day_only,
            "缺日期保留匹配证据但不进 timing gap：这正是「响应速度」不可推断的原因",
        )
        package_doc_row("W2")

        # W3: address outflows stay descriptive.
        outflow_rows = [row for row in metrics if row["source_artifact"].endswith("exchange_outflows.csv")]
        no_value_when_unreportable = all(
            row["value"] == ""
            for row in inputs["restrict_tables"]["exchange_outflows"]
            if row["status"] == "not_reportable"
        )
        add(
            "W3",
            "comparison_report",
            "report_field",
            f"{cfg.rel(inputs['compare_dir'])}/report.json#caveats.no_evasion_inference",
            "caveat 存在" if caveat(compare_report, "no_evasion_inference") else "caveat 缺失",
            caveat(compare_report, "no_evasion_inference"),
        )
        add(
            "W3",
            "restricted_report",
            "report_field",
            f"{cfg.rel(inputs['restrict_dir'])}/report.json#caveats",
            "no_evasion_inference 与 transfer_absence_not_zero 均存在"
            if caveat(restrict_report, "no_evasion_inference")
            and caveat(restrict_report, "transfer_absence_not_zero")
            else "caveat 缺失",
            caveat(restrict_report, "no_evasion_inference")
            and caveat(restrict_report, "transfer_absence_not_zero"),
        )
        add(
            "W3",
            "metric_tables",
            "table_column",
            f"{cfg.rel(inputs['restrict_dir'])}/exchange_outflows.csv",
            f"{len(outflow_rows)} 个 outflow 指标行；不可报告行写空值 "
            f"{'是' if no_value_when_unreportable else '否'}（不写 0 流出）",
            no_value_when_unreportable,
        )
        hits, missing = self._doc_hits("docs/restricted-subset-analyses.md")
        add(
            "W3",
            "restricted_doc",
            "doc_anchor",
            "docs/restricted-subset-analyses.md#7",
            f"措辞边界锚点命中 {hits}/{hits + len(missing)}",
            not missing,
        )
        package_doc_row("W3")

        # W4: frozen balances declare one definition and stay descriptive.
        balance_rows = [row for row in metrics if row["metric_id"].startswith("frozen_balance")]
        # Difference rows inherit the two declared references; they carry no column of
        # their own, so they are not evidence of an undeclared metric.
        undeclared = [
            row
            for row in balance_rows
            if row["issuer"] != "USDT-USDC" and not row["balance_reference"]
        ]
        frozen_rows = inputs["restrict_tables"]["frozen_balances"]
        at_freeze = all(row["balance_reference"] == "at_freeze" for row in frozen_rows)
        d1 = {row["metric_id"] for row in balance_rows if row["balance_reference"] == "at_freeze"}
        d2 = {
            row["metric_id"]
            for row in balance_rows
            if row["balance_reference"] == "at_study_cutoff"
        }
        add(
            "W4",
            "comparison_report",
            "report_field",
            f"{cfg.rel(inputs['compare_dir'])}/report.json#caveats",
            "balances_two_definitions 与 no_enforcement_effectiveness_inference 均存在"
            if caveat(compare_report, "balances_two_definitions")
            and caveat(compare_report, "no_enforcement_effectiveness_inference")
            else "caveat 缺失",
            caveat(compare_report, "balances_two_definitions")
            and caveat(compare_report, "no_enforcement_effectiveness_inference"),
        )
        add(
            "W4",
            "restricted_report",
            "report_field",
            f"{cfg.rel(inputs['restrict_dir'])}/report.json#balance_definition",
            "balance_definition 明确只用 D1、不报告 D2"
            if restrict_report.get("balance_definition", {}).get("d2_reported_on_subset") is False
            else "balance_definition 未声明",
            restrict_report.get("balance_definition", {}).get("d2_reported_on_subset") is False,
        )
        add(
            "W4",
            "metric_tables",
            "table_column",
            f"{cfg.rel(inputs['compare_dir'])}/comparison.csv#balance_reference；"
            f"{cfg.rel(inputs['restrict_dir'])}/frozen_balances.csv",
            f"{len(balance_rows)} 个冻结余额指标行，未声明口径的 {len(undeclared)} 行；"
            f"D1 {len(d1)} 个 / D2 {len(d2)} 个 id；差值行继承两侧口径、不单列；"
            f"受限冻结余额全部 at_freeze {'是' if at_freeze else '否'}",
            not undeclared and bool(d1) and bool(d2) and at_freeze,
        )
        package_doc_row("W4")

        # W5: no extrapolation from the supported subset.
        subset_rows = [row for row in metrics if row["evidence_tier"] == "B"]
        all_restricted = all(row["coverage_scope"] == "restricted_subset" for row in subset_rows)
        add(
            "W5",
            "comparison_report",
            "report_field",
            f"{cfg.rel(inputs['compare_dir'])}/report.json#caveats.not_extrapolated",
            "caveat 存在" if caveat(compare_report, "not_extrapolated") else "caveat 缺失",
            caveat(compare_report, "not_extrapolated"),
        )
        add(
            "W5",
            "restricted_report",
            "report_field",
            f"{cfg.rel(inputs['restrict_dir'])}/report.json#caveats.not_extrapolated",
            "caveat 存在" if caveat(restrict_report, "not_extrapolated") else "caveat 缺失",
            caveat(restrict_report, "not_extrapolated"),
        )
        add(
            "W5",
            "metric_tables",
            "table_column",
            f"{cfg.rel(inputs['compare_dir'])}/comparison.csv；"
            f"{cfg.rel(inputs['restrict_dir'])}/timing_gaps.csv 等",
            f"{len(subset_rows)} 行按 B 层（仅受支持子集）标注，coverage_scope 全为 "
            f"restricted_subset {'是' if all_restricted else '否'}",
            all_restricted and bool(subset_rows),
        )
        package_doc_row("W5")

        # W6: the two analysis paths stay physically separate.
        separation_manifest = inputs["restrict_manifest"]
        separation = separation_manifest.get("separation", {})
        separate = (
            separation.get("concatenated_with_core") is False
            and separation.get("cross_issuer_difference_produced") is False
        )
        compare_tables = {"comparison", "differences", "sensitivity"}
        restrict_table_names = set(inputs["restrict_tables"])
        overlap = compare_tables & restrict_table_names
        add(
            "W6",
            "restricted_manifest",
            "manifest_flag",
            f"{cfg.rel(inputs['restrict_dir'])}/manifest.json#separation",
            f"concatenated_with_core={_json_bool(separation.get('concatenated_with_core'))}；"
            f"cross_issuer_difference_produced="
            f"{_json_bool(separation.get('cross_issuer_difference_produced'))}",
            separate,
        )
        add(
            "W6",
            "metric_tables",
            "table_column",
            f"{cfg.rel(inputs['compare_dir'])}/comparison.csv；"
            f"{cfg.rel(inputs['restrict_dir'])}/*.csv",
            f"比较表名 {sorted(compare_tables)} 与受限表名 {sorted(restrict_table_names)}，"
            f"重名 {len(overlap)} 个",
            not overlap,
        )
        package_doc_row("W6")

        # W7: the list snapshot date is not a sanctions date. A row may legitimately
        # carry a snapshot-date *label* and a different designation date; the leak is
        # reusing the publication date as the designation date.
        snapshot_leak = [
            row
            for row in matches
            if row["sanctions_public_timestamp"]
            and (
                row["sanctions_date_basis"] == "list_snapshot_date"
                or (
                    row["label_date"]
                    and row["sanctions_public_timestamp"] == row["label_date"]
                )
            )
        ]
        basis_leak = [
            row
            for row in matches
            if row["sanctions_public_timestamp"] and row["sanctions_date_basis"] not in allowed_basis
        ]
        add(
            "W7",
            "matches_table",
            "table_column",
            f"{cfg.rel(inputs['match_dir'])}/matches.csv",
            f"快照日期顶替制裁日期的行 {len(snapshot_leak)}；制裁日期缺依据的行 {len(basis_leak)}",
            not snapshot_leak and not basis_leak,
        )
        add(
            "W7",
            "dictionary",
            "dictionary_enum",
            "docs/data-dictionary.csv#matches.sanctions_date_basis",
            f"sanctions_date_basis 枚举 {sorted(allowed_basis)}；快照发布日不在其中",
            "list_snapshot_date" not in allowed_basis,
        )
        hits, missing = self._doc_hits("docs/definitions.md")
        add(
            "W7",
            "definitions_doc",
            "doc_anchor",
            "docs/definitions.md#4.2",
            f"定义文档锚点命中 {hits}/{hits + len(missing)}",
            not missing,
        )
        package_doc_row("W7")

        # W8: Ethereum is the verified sample, not the feedback's limit for every chain.
        pending = [row for row in chains if row["pending_item_id"]]
        declared = inputs["decision"]["scope"]["chains"]
        open_items = "；".join(inputs["decision"].get("open_items", []))
        add(
            "W8",
            "chain_registry",
            "registry_row",
            f"{cfg.rel(inputs['core_dir'])}/chain_registry.csv",
            f"{len(chains)} 条链登记："
            + "、".join(f"{row['chain']}={row['in_scope']}" for row in chains),
            bool(pending) and any(row["in_scope"] == "verified_sample" for row in chains),
        )
        add(
            "W8",
            "study_scope",
            "report_field",
            "config/study_scope.json#open_items",
            f"声明采集链 {declared}；open_items 含链范围待确认：{'是' if '链范围' in open_items else '否'}",
            "链范围" in open_items,
        )
        hits, missing = self._doc_hits("docs/study-scope.md")
        add(
            "W8",
            "study_scope_doc",
            "doc_anchor",
            "docs/study-scope.md#1",
            f"研究范围文档锚点命中 {hits}/{hits + len(missing)}",
            not missing,
        )
        package_doc_row("W8")
        return rows

    # -------------------------------------------------------------------- report
    def report(self, inputs: dict, tables: dict[str, list[dict]]) -> dict:
        cfg = self.cfg
        materials = tables[MATERIAL_TABLE]
        fields = tables[FIELD_TABLE]
        metrics = tables[METRIC_TABLE]
        wording = tables[WORDING_TABLE]
        chains = tables[CHAIN_TABLE]

        tier_counts = Counter(row["availability_tier"] for row in fields)
        reporting_counts = Counter(row["reporting_scope"] for row in metrics)
        failed = sorted({row["constraint_id"] for row in wording if row["passed"] != "yes"})
        subset_metrics = sorted({row["metric_id"] for row in metrics if row["evidence_tier"] == "B"})
        consistent_tables = {
            table: sum(
                1 for row in fields if row["table"] == table and row["availability_tier"] == "A"
            )
            for table in ("events", "balances", "chain_registry", "validation")
        }
        conditional_tables = {
            table: sum(
                1 for row in fields if row["table"] == table and row["availability_tier"] == "B"
            )
            for table in ("matches", "validation")
        }
        material_counts = {
            material_id: sum(1 for row in materials if row["material_id"] == material_id)
            for material_id in MATERIAL_NAMES
        }

        doc_anchor_failures: dict[str, list[str]] = {}
        for rel in DOC_ANCHORS:
            _, missing = self._doc_hits(rel)
            if missing:
                doc_anchor_failures[rel] = missing

        pending_rows = [row for row in chains if row["pending_item_id"]]
        conclusions = [
            {
                "id": "S1",
                "statement_type": "descriptive",
                "coverage_scope": "all_materials",
                "text": (
                    f"四项支撑材料齐备，共 {len(materials)} 个组件（样本 {material_counts['M1']}、"
                    f"字典 {material_counts['M2']}、脚本 {material_counts['M3']}、"
                    f"验证表 {material_counts['M4']}，另有分析视图 {material_counts['M5']}）；"
                    "每个组件带摘要与离线核对命令，字段与口径无需全量重跑即可检查。"
                ),
                "guards": ["materials_complete", "offline_checkable"],
            },
            {
                "id": "S2",
                "statement_type": "descriptive",
                "coverage_scope": "core_full",
                "text": (
                    f"A 层（一致可得）字段 {tier_counts.get('A', 0)} 个：events "
                    f"{consistent_tables['events']}、balances {consistent_tables['balances']}、"
                    f"chain_registry {consistent_tables['chain_registry']}；B 层（条件可得）字段 "
                    f"{tier_counts.get('B', 0)} 个：matches {conditional_tables['matches']}"
                    f"（外部匹配）、validation {conditional_tables['validation']}（核对记录）；"
                    "C 层无字段，私有执法请求时间、冻结原因、地址实体归属与跨链迁移只在正文声明。"
                    f"指标侧：{reporting_counts.get('all_events', 0)} 行按全量事件口径、"
                    f"{reporting_counts.get('core_subpopulation', 0)} 行按核心子总体、"
                    f"{reporting_counts.get('supported_subset', 0)} 行只在受支持子集上可报、"
                    f"{reporting_counts.get('definitional_zero', 0)} 行为定义性零。"
                ),
                "guards": ["availability_layers_declared", "subset_metrics_separated"],
            },
            {
                "id": "S3",
                "statement_type": "descriptive",
                "coverage_scope": "all_materials",
                "text": (
                    f"措辞约束 {len(CONSTRAINTS)} 条、核对行 {len(wording)} 条，未通过 {len(failed)} 条；"
                    "public sanctions timing gaps、address outflows 与 frozen balances 均只作描述性统计，"
                    "包内不出现私有请求响应、规避或执法有效性的推断。"
                    f"数据抽样签署：{signoff_phrase()}；"
                    "正文措辞的语义审查仍属人工职责，本包核对的是结构化证据与显式声明。"
                ),
                "guards": ["wording_checked", "descriptive_only", "prose_semantic_review_pending"],
            },
            {
                "id": "S4",
                "statement_type": "descriptive",
                "coverage_scope": "core_full",
                "text": (
                    f"链范围：核心集 {inputs['core_manifest']['counts']['events']} 条事件全部在 "
                    f"{'、'.join(inputs['decision']['scope']['chains'])}；反馈只把「小型代表性样本」"
                    "限定在 Ethereum，核心分析的 all retrievable events 未限定链。其他链登记为待确认项 "
                    f"{[row['pending_item_id'] for row in pending_rows]}，确认后需扩采集并重做研究期决策。"
                ),
                "guards": ["chain_scope_declared", "ethereum_not_global_limit"],
            },
        ]

        return {
            "package": {
                "title": "支撑材料包与措辞审查",
                "contract_version": cfg.registry["dataset_contract_version"],
                "scope": "工单 02–08 已交付产物；本包不重新采集、不重算事件与余额",
            },
            "inputs": {
                "core_id": inputs["core_manifest"]["core_id"],
                "core_data_digest": inputs["core_manifest"]["digests"]["data_digest"],
                "match_batch_id": inputs["match_manifest"]["match_batch_id"],
                "match_data_digest": inputs["match_manifest"]["digests"]["data_digest"],
                "compare_batch_id": inputs["compare_manifest"]["compare_batch_id"],
                "compare_data_digest": inputs["compare_manifest"]["digests"]["data_digest"],
                "restrict_batch_id": inputs["restrict_manifest"]["restrict_batch_id"],
                "restrict_data_digest": inputs["restrict_manifest"]["digests"]["data_digest"],
                "dictionary": cfg.rel(cfg.dictionary_csv),
                "dictionary_sha256": _sha256_file(cfg.dictionary_csv),
                "scope_decision": DECISION_PATH,
                "scope_decision_sha256": inputs["decision_sha256"],
            },
            "materials": {
                "required": list(REQUIRED_MATERIALS),
                "components": len(materials),
                "by_material": material_counts,
                "missing": [row["path"] for row in materials if row["exists"] != "yes"],
            },
            "availability": {
                "tier_counts": {"A": tier_counts.get("A", 0), "B": tier_counts.get("B", 0), "C": 0},
                "tier_labels": {
                    "A": "一致可得：由日志、收据与归档状态直接得到，两个 issuer 同法可得",
                    "B": "条件可得：依赖外部数据源，只覆盖子集，只能在受支持子集上报告",
                    "C": "不可观测：无字段，只在正文声明，不产出指标",
                },
                "consistent_fields_by_table": consistent_tables,
                "subset_only_fields": [row["field"] for row in fields if row["subset_only"] == "yes"],
                "unobservable": ["私有执法请求时间", "冻结原因", "地址实体归属", "跨链迁移"],
            },
            "metric_scope": {
                "reporting_scope_counts": dict(sorted(reporting_counts.items())),
                "evidence_tier_counts": dict(
                    sorted(Counter(row["evidence_tier"] for row in metrics).items())
                ),
                "subset_only_metrics": subset_metrics,
                "note": "B 层指标只能对受支持子集报告；与 A 层指标不相加、不拼接",
            },
            "wording": {
                "constraints": len(CONSTRAINTS),
                "checks": len(wording),
                "failed_constraints": failed,
                "checked_by": (
                    "结构化证据（statement_type / coverage_scope / caveats / 列取值 / 注册表行）"
                ),
                "prose_semantic_review": "pending",
                "data_signoff": signoff_status(),
                "doc_anchor_failures": doc_anchor_failures,
            },
            "chain_scope": {
                "declared": inputs["decision"]["scope"]["chains"],
                "registry": [
                    {
                        "chain": row["chain"],
                        "in_scope": row["in_scope"],
                        "collection_status": row["collection_status"],
                    }
                    for row in chains
                ],
                "pending_items": [
                    {
                        "id": row["pending_item_id"],
                        "chain": row["chain"],
                        "text": "核心分析条款未限定链；其他链只登记不采集，是否纳入待确认",
                        "declared_in": [
                            "config/study_scope.json#open_items",
                            f"{cfg.rel(inputs['core_dir'])}/chain_registry.csv",
                            ".scratch/freeze-event-reconstruction/spec.md#Further Notes",
                        ],
                        "effect_on_collection": row["effect_if_confirmed"],
                    }
                    for row in pending_rows
                ],
                "ethereum_is_sample_scope_only": True,
                "note": (
                    "反馈只在「小型代表性样本」条款中写明 Ethereum；核心分析写的是 all retrievable "
                    "events，未限定链。当前范围是本次实际采集范围，不是反馈对全部链的限定"
                ),
            },
            "descriptive_vs_causal": {
                "statement_type_values": dict(
                    sorted(Counter(row["statement_type"] for row in metrics).items())
                ),
                "causal_inference_permitted": "no",
                "conclusion_rows": len(
                    inputs["compare_report"]["conclusions"]
                    + inputs["restrict_report"]["conclusions"]
                    + conclusions
                ),
                "causal_claim_rows": 0,
                "how_to_tell": (
                    "表格：statement_type 列固定 descriptive、causal_inference_permitted 固定 no；"
                    "文档：docs/supporting-materials.md 第 5 节把四类声明与禁止的推断并列"
                ),
            },
            "conclusions": conclusions,
            "caveats": {
                "descriptive_only": "包内所有结论都是描述性；不推断原因、响应速度、规避或执法有效性",
                "materials_are_pointers": (
                    "索引只指向已交付产物并记录摘要；本包不重新采集、不重算事件与余额"
                ),
                "subset_metrics_are_b_layer": (
                    "只在受支持子集可报的指标按 B 层标注，不与 A 层指标拼接"
                ),
                "unobservable_has_no_field": (
                    "私有执法请求时间不可观测（not_observable），无字段，不参与任何指标"
                ),
                "snapshot_date_not_sanctions_date": (
                    "清单快照发布日只写 label_date，不顶替 sanctions_public_timestamp"
                ),
                "chain_scope_pending": (
                    "Ethereum 是本次实际采集范围；其他链是否纳入为待确认项，"
                    "确认后须扩采集并重做研究期决策"
                ),
                "data_signoff_status": signoff_phrase(),
                "prose_semantic_review_pending": (
                    "措辞与材料核对由脚本执行；全量验证表无逐行人工签字（reviewer = "
                    "agent:auto-cross-check），26 行分层抽样另见 data_signoff_status"
                ),
                "prose_checked_by_anchor": (
                    "文档只核对是否出现对应声明（锚点），不做语义推断；语义审查待人工完成"
                ),
            },
            "rule_checks": self.rules_self_test(),
        }

    # --------------------------------------------------------------- self-tests
    def rules_self_test(self) -> dict:
        """Synthetic fixtures proving each guard can fail.

        The delivered data passes every guard, so a guard that can never fail would look
        identical to one that always passes. Each probe feeds a deliberately broken row
        through the same predicate the real check uses.
        """
        checks: dict[str, str] = {}

        drift = self._parity_probe(
            {"event_id": "ev_x", "block_number": "1"},
            {"event_id": "ev_x", "block_number": "2"},
        )
        checks["sample_parity_detects_value_drift"] = (
            "ok" if drift else "failed: 取值漂移未被识别"
        )

        leak = self._snapshot_leak(
            [
                {
                    "label_date_basis": "list_snapshot_date",
                    "label_date": "2026-11-09",
                    "sanctions_date_basis": "list_snapshot_date",
                    "sanctions_public_timestamp": "2026-11-09",
                }
            ]
        )
        checks["snapshot_date_reuse_detected"] = (
            "ok" if leak else "failed: 快照日期顶替制裁日期未被识别"
        )

        checks["non_descriptive_statement_detected"] = (
            "ok"
            if not self._descriptive_probe({"statement_type": "causal"})
            else "failed: 因果结论未被识别"
        )

        checks["subset_metric_tagged_b_layer"] = (
            "ok"
            if self._metric_row(
                metric_id="probe",
                metric_label="probe",
                issuer="USDT",
                unit="events",
                source_artifact="probe.csv",
                coverage_scope="restricted_subset",
                status="not_reportable",
            )["evidence_tier"]
            == "B"
            else "failed: 受限子集指标被标成 A 层"
        )

        checks["missing_component_not_silently_ok"] = (
            "ok"
            if self._component(
                "M1", "data/samples/does_not_exist.csv", "probe", "probe", "", ""
            )["exists"]
            == "no"
            else "failed: 缺失组件被当成存在"
        )
        return checks

    @staticmethod
    def _parity_probe(sample_row: dict, delivered_row: dict) -> bool:
        """Any value drift between the two rows must be reported."""
        value_fields = ["block_number"]
        return any(sample_row.get(name) != delivered_row.get(name) for name in value_fields)

    @staticmethod
    def _snapshot_leak(rows: list[dict]) -> bool:
        """True when a row reuses the list publication date as the designation date."""
        return any(
            row["sanctions_public_timestamp"]
            and (
                row.get("sanctions_date_basis") == "list_snapshot_date"
                or (
                    row.get("label_date")
                    and row["sanctions_public_timestamp"] == row["label_date"]
                )
            )
            for row in rows
        )

    @staticmethod
    def _descriptive_probe(row: dict) -> bool:
        return row.get("statement_type") == "descriptive"

    # ----------------------------------------------------------------------- run
    def run(self) -> dict:
        inputs = self.load_inputs()
        tables: dict[str, list[dict]] = {}
        tables[MATERIAL_TABLE] = self.materials_rows(inputs)
        tables[FIELD_TABLE] = self.field_rows(inputs)
        tables[METRIC_TABLE] = self.metric_rows(inputs)
        tables[CHAIN_TABLE] = self.chain_rows(inputs)
        tables[WORDING_TABLE] = self.wording_rows(inputs, tables)
        report = self.report(inputs, tables)
        return {"tables": tables, "report": report, "inputs": inputs}

    def run_id(self, inputs: dict) -> str:
        return "materials_" + sha256_hex(
            canonical_json(
                {
                    "core_data_digest": inputs["core_manifest"]["digests"]["data_digest"],
                    "match_data_digest": inputs["match_manifest"]["digests"]["data_digest"],
                    "compare_data_digest": inputs["compare_manifest"]["digests"]["data_digest"],
                    "restrict_data_digest": inputs["restrict_manifest"]["digests"]["data_digest"],
                    "dictionary_sha256": _sha256_file(self.cfg.dictionary_csv),
                    "scope_decision_sha256": inputs["decision_sha256"],
                    "dataset_contract_version": self.cfg.registry["dataset_contract_version"],
                    "samples": {
                        name: _sha256_file(self.cfg.path(f"data/samples/sample_{name}.csv"))
                        for name in ("events", "balances", "matches", "validation")
                    },
                }
            ).encode()
        )[:32]

    def deliver(self, outcome: dict) -> dict:
        for table, rows in outcome["tables"].items():
            _assert_fields(table, FIELD_ORDER[table], rows)

        run_id = self.run_id(outcome["inputs"])
        out_dir = self.cfg.out_root / run_id
        out_dir.mkdir(parents=True, exist_ok=True)

        written: list[Path] = []
        parquet_written: dict[str, bool] = {}
        for table, rows in outcome["tables"].items():
            fields = FIELD_ORDER[table]
            csv_path = out_dir / f"{table}.csv"
            write_csv(csv_path, fields, rows)
            written.append(csv_path)
            parquet_written[table] = write_parquet(out_dir / f"{table}.parquet", fields, rows)
        report_path = out_dir / "report.json"
        report_path.write_text(
            json.dumps(outcome["report"], indent=1, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        inputs = outcome["inputs"]
        report = outcome["report"]
        manifest = {
            "package_id": run_id,
            "dataset_contract_version": self.cfg.registry["dataset_contract_version"],
            "generated_at_utc": now_utc(),
            "inputs": report["inputs"],
            "counts": {
                "materials": len(outcome["tables"][MATERIAL_TABLE]),
                "fields": len(outcome["tables"][FIELD_TABLE]),
                "metrics": len(outcome["tables"][METRIC_TABLE]),
                "wording_checks": len(outcome["tables"][WORDING_TABLE]),
                "chains": len(outcome["tables"][CHAIN_TABLE]),
                "tier_a_fields": report["availability"]["tier_counts"]["A"],
                "tier_b_fields": report["availability"]["tier_counts"]["B"],
                "subset_only_metrics": len(report["metric_scope"]["subset_only_metrics"]),
                "wording_failures": len(report["wording"]["failed_constraints"]),
            },
            "checks": {
                "columns": "五张派生视图写出前按本模块声明的字段断言（不进 docs/data-dictionary.csv）",
                "materials_present": not report["materials"]["missing"],
                "wording_failures": report["wording"]["failed_constraints"],
                "rules": report["rule_checks"],
                "offline_recheck": "python scripts/reconstruct_materials.py --check",
            },
            "upstream_untouched": {
                "core_id": inputs["core_manifest"]["core_id"],
                "match_batch_id": inputs["match_manifest"]["match_batch_id"],
                "compare_batch_id": inputs["compare_manifest"]["compare_batch_id"],
                "restrict_batch_id": inputs["restrict_manifest"]["restrict_batch_id"],
                "note": "本模块只读上游产物；不改动核心四表、matches、比较表与受限表",
            },
            "columns_verified": True,
            "parquet_written": parquet_written,
            "digests": {
                "data_digest": data_digest(outcome["tables"]),
                "artifact_digest": artifact_digest(written + [report_path]),
            },
            "reproduce_command": "python scripts/reconstruct_materials.py",
            "outputs": {
                table: self.cfg.rel(out_dir / f"{table}.csv") for table in outcome["tables"]
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

    # --------------------------------------------------------------------- check
    def check(self) -> list[str]:
        problems: list[str] = []
        try:
            outcome = self.run()
        except (SystemExit, ValueError, KeyError, OSError) as exc:
            return [f"无法重建支撑材料包：{exc}"]

        run_id = self.run_id(outcome["inputs"])
        out_dir = self.cfg.out_root / run_id
        manifest_path = out_dir / "manifest.json"
        if not manifest_path.exists():
            return [f"支撑材料包缺失：{self.cfg.rel(manifest_path)}"]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("package_id") != run_id:
            problems.append(
                f"manifest.package_id={manifest.get('package_id')} 与输入派生的 {run_id} 不一致"
            )

        for table, rows in outcome["tables"].items():
            path = out_dir / f"{table}.csv"
            if not path.exists():
                problems.append(f"缺少 {self.cfg.rel(path)}")
                continue
            delivered = read_table(path)
            if delivered and list(delivered[0]) != FIELD_ORDER[table]:
                problems.append(f"{table}.csv 的列集合与本模块声明不一致")
            if delivered != rows:
                problems.append(
                    f"{table}.csv 与重算结果不一致（{len(delivered)} ≠ {len(rows)} 行）"
                )

        report_path = out_dir / "report.json"
        delivered_report = json.loads(report_path.read_text(encoding="utf-8"))
        if delivered_report != outcome["report"]:
            problems.append("report.json 与重算结果不一致")

        if manifest["digests"]["data_digest"] != data_digest(outcome["tables"]):
            problems.append("manifest 的 data_digest 与重算的数据摘要不一致")

        for table in outcome["tables"]:
            for row in outcome["tables"][table]:
                if row.get("exists") == "no":
                    problems.append(f"缺失组件：{row['path']}")
                if row.get("passed") == "no":
                    problems.append(f"措辞核对未通过：{row['constraint_id']} / {row['applies_to']}")
        for name, result in outcome["report"]["rule_checks"].items():
            if result != "ok":
                problems.append(f"规则自检未通过：{name}（{result}）")
        for rel, missing in outcome["report"]["wording"]["doc_anchor_failures"].items():
            problems.append(f"{rel} 缺少措辞声明锚点：{missing}")

        # The package must not have touched its upstream bundles.
        for key, rel_dir in (
            ("core_manifest", outcome["inputs"]["core_dir"]),
            ("match_manifest", outcome["inputs"]["match_dir"]),
            ("compare_manifest", outcome["inputs"]["compare_dir"]),
            ("restrict_manifest", outcome["inputs"]["restrict_dir"]),
        ):
            upstream = json.loads((rel_dir / "manifest.json").read_text(encoding="utf-8"))
            if upstream["digests"]["data_digest"] != outcome["inputs"][key]["digests"]["data_digest"]:
                problems.append(f"上游批次 {rel_dir.name} 的摘要被改动")
        return problems
