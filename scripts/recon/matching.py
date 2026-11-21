"""Matching layer and missingness report over the core event set (ticket 06).

The core event set (ticket 05) answers "what happened". Whether an address is a
public sanctions record or a known exchange is a *different* question, answered by
a different source, and it must not leak into the event set. So this module reads
the delivered core bundle and writes a separate ``matches`` table plus a report:

* matching never filters: ``events.analysis_eligibility`` is not read here, no event
  is dropped, and the core bundle is only ever read;
* a failed or missing match is recorded as a failure (``unmatched`` /
  ``not_attempted`` / ``not_applicable`` plus a reason code), never as a zero and
  never as a match;
* a label without a date cannot explain a past event (R5): it stays in the table
  with an empty ``label_date`` and an empty match effect on dated analyses, and a
  label dated *after* the event is recorded as ``not_applicable`` with its evidence
  kept, so nobody can use it to interpret that event.

Two layers, both pinned to a snapshot on disk:

``ofac_sdn``
    Exact address-string membership in ``data/raw/ofac`` (SDN digital currency
    extract); ``label_date`` gets the snapshot date (``list_snapshot_date``).
    ``sanctions_public_timestamp`` comes from a second, separately pinned source:
    the entity ``EntryEvent`` dates of the SDN Advanced XML
    (``config/ofac_advanced.json``), recorded with ``designation_date`` / ``day``.
    The date is entity-level -- every address of one entity shares it. An address
    absent from that extract stays empty with ``label_date_missing`` and never
    receives the snapshot date.

``exchange_label``
    Dated historical labels from ``config/exchange_labels.json``. No dated label
    source is available in this repo, so every row is ``not_attempted`` with
    ``external_source_unavailable`` -- reported as not reportable rather than as a
    0% rate.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .core import locate_core_bundle
from .pipeline import load_ofac_index
from .rpc import canonical_json, now_utc, sha256_hex
from .tables import (
    PROVENANCE_TIME_FIELDS,
    assert_columns,
    artifact_digest,
    data_digest,
    load_dictionary,
    ratio,
    read_table,
    share,
    write_csv,
    write_parquet,
)

LABEL_PATH = "config/exchange_labels.json"
CORE_TABLE_ORDER = ("events", "balances", "validation", "chain_registry")
MATCH_TABLE = "matches"
LAYERS = ("ofac_sdn", "exchange_label")
OFAC_LAYER = "ofac_sdn"
LABEL_LAYER = "exchange_label"
# 与 data/samples/sample_matches.csv 的来源名保持一致，避免样例与交付表出现两套叫法。
OFAC_SOURCE_NAME = "OFAC SDN List (SDN.CSV)"
OFAC_SOURCE_URL = (
    "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.CSV"
)
LABEL_DATE_BASES = {
    "label_publication_date",
    "list_snapshot_date",
    "observed_first_seen_date",
}
MATCH_REASONS = {
    "sanctions_record_missing",
    "external_source_unavailable",
    "label_date_missing",
    "not_applicable",
}
OBJECTIVES = (
    "public-record match rates",
    "missing values",
    "label dates",
)

# The report must say all of this in words, and ``--check`` refuses to pass when a
# statement silently disappears. Keys are stable so a reviewer can diff them.
REQUIRED_CAVEATS = (
    "matches_do_not_filter",
    "unmatched_is_not_absence_of_sanctions",
    "missingness_is_not_zero",
    "snapshot_postdates_events",
    "label_dates_are_load_bearing",
    "coverage_not_extrapolable",
    "private_request_not_observable",
)


def _rate(numerator: int, denominator: int) -> dict:
    """A rate that refuses to exist when nothing was attempted.

    ``rate = None`` with ``rate_status = not_reportable`` is the honest answer for
    "no label source": writing 0 would claim we looked and found nothing.
    """
    value = share(numerator, denominator)
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": value,
        "rate_status": "reported" if value is not None else "not_reportable",
    }


def required_fields(path: Path) -> dict[str, set[str]]:
    """Dictionary fields marked ``required = yes``: an empty one is a real gap.

    Every other empty field is optional by contract, so a missingness report that
    counted it would overstate the gap (e.g. ``destruction_amount_raw`` on a
    ``freezing`` row is empty *by definition*).
    """
    required: dict[str, set[str]] = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            table = (row.get("table") or "").strip()
            field_name = (row.get("field") or "").strip()
            if not table or not field_name:
                continue
            if (row.get("required") or "").strip().lower() == "yes":
                required.setdefault(table, set()).add(field_name)
    return required


def normalise_date(text: object) -> str:
    """``11/09/2026`` / ``2026-11-09`` / ``2026-11-09T00:00:00Z`` -> ``2026-11-09``."""
    value = str(text or "").strip()
    if not value:
        return ""
    if "/" in value:
        parts = value.split("/")
        if len(parts) == 3 and len(parts[2]) == 4:
            month, day, year = parts
            return f"{year}-{int(month):02d}-{int(day):02d}"
        return ""
    if len(value) >= 10 and value[4] == "-" and value[7] == "-":
        return value[:10]
    return ""


def event_date(event: dict) -> str:
    return str(event.get("block_time_utc", ""))[:10]


def match_id(event_id: str, layer: str, source: str) -> str:
    return "mt_" + sha256_hex(f"{event_id}|{layer}|{source}".encode())[:32]


@dataclass
class LabelRegistry:
    """Historical exchange labels, each one carrying its own date."""

    path: str
    sha256: str
    sources: dict[str, dict]
    labels: dict[str, dict]
    notes: list[str] = field(default_factory=list)

    @property
    def attempted(self) -> bool:
        """Whether a label source exists at all (different from "no label for X")."""
        return bool(self.sources)

    def source(self, name: str) -> dict:
        return self.sources.get(name, {})


def load_labels(cfg: Config, relative: str) -> LabelRegistry:
    """Parse the label registry; a malformed entry stops the run instead of degrading it."""
    path = cfg.path(relative)
    if not path.exists():
        return LabelRegistry(
            path=relative,
            sha256="",
            sources={},
            labels={},
            notes=[
                f"标签注册表不存在：{relative}；按「无标签来源」处理，不推断任何标签"
            ],
        )

    raw = path.read_bytes()
    document = json.loads(raw.decode("utf-8"))
    sources: dict[str, dict] = {}
    for source in document.get("sources", []):
        name = str(source.get("name", "")).strip()
        if not name:
            raise SystemExit(f"{relative}: 标签来源缺少 name")
        snapshot_sha = str(source.get("snapshot_sha256", "")).strip()
        if not snapshot_sha:
            # 没有快照摘要就没有可复核的证据：匹配行会带一个空摘要，不允许。
            raise SystemExit(f"{relative}: 标签来源 {name!r} 缺少 snapshot_sha256")
        sources[name] = {
            "name": name,
            "url": str(source.get("url", "")).strip(),
            "snapshot_sha256": snapshot_sha,
            "retrieved_at_utc": str(source.get("retrieved_at_utc", "")).strip(),
        }

    labels: dict[str, dict] = {}
    notes: list[str] = []
    for entry in document.get("labels", []):
        address = str(entry.get("address", "")).strip().lower()
        if not (address.startswith("0x") and len(address) == 42):
            raise SystemExit(f"{relative}: 标签地址不可用：{entry.get('address')!r}")
        if address in labels:
            raise SystemExit(f"{relative}: 标签地址重复：{address}")
        label_date = normalise_date(entry.get("label_date"))
        basis = str(entry.get("label_date_basis", "")).strip()
        if label_date and basis not in LABEL_DATE_BASES:
            raise SystemExit(
                f"{relative}: {address} 的 label_date_basis={basis!r} 不在字典枚举内"
            )
        if not label_date and basis:
            raise SystemExit(f"{relative}: {address} 有 label_date_basis 但没有 label_date")
        source_name = str(entry.get("source_name", "")).strip()
        if not sources:
            # 有标签却没有来源：不能静默降级成 not_attempted，那等于把标签丢掉。
            raise SystemExit(f"{relative}: 登记了标签但 sources 为空，无法出具证据")
        if source_name not in sources:
            raise SystemExit(
                f"{relative}: {address} 的 source_name={source_name!r} 未在 sources 登记"
            )
        labels[address] = {
            "address": address,
            "entity_name": str(entry.get("entity_name", "")).strip(),
            "record_id": str(entry.get("record_id", "")).strip(),
            "label_date": label_date,
            "label_date_basis": basis,
            "evidence_text": str(entry.get("evidence_text", "")).strip(),
            "source_name": source_name or "exchange_label_registry",
        }

    undated = sum(1 for label in labels.values() if not label["label_date"])
    if undated:
        notes.append(
            f"标签注册表有 {undated} 条无日期标签：按 R5 只参与 missing-label 敏感性，"
            "不进入带日期分析"
        )
    return LabelRegistry(
        path=relative,
        sha256=sha256_hex(raw),
        sources=sources,
        labels=labels,
        notes=notes,
    )


SANCTIONS_DATES_PATH = "config/ofac_advanced.json"
SANCTIONS_DATE_BASES = {"designation_date"}
SANCTIONS_DATE_PRECISIONS = {"day"}


@dataclass
class SanctionsDates:
    """Entity-level designation dates, pinned to one extract of the SDN Advanced XML."""

    path: str
    source_path: str
    sha256: str
    basis: str
    precision: str
    by_address: dict[str, dict]
    notes: list[str] = field(default_factory=list)

    @property
    def registered(self) -> bool:
        """Whether a dated source is registered at all (different from "no date for X")."""
        return bool(self.sha256)

    def date_for(self, address: str) -> dict | None:
        return self.by_address.get(str(address).strip().lower())


def load_sanctions_dates(cfg: Config, relative: str = SANCTIONS_DATES_PATH) -> SanctionsDates:
    """Read the registered date extract; a digest mismatch stops the run.

    The date extract is a *different snapshot* from the SDN list used for the
    address match, so it is pinned separately and recorded in the match manifest.
    Nothing is inferred when an address is absent: the date stays empty and the
    row keeps ``label_date_missing``.
    """
    path = cfg.path(relative)
    if not path.exists():
        return SanctionsDates(
            path=relative,
            source_path="",
            sha256="",
            basis="",
            precision="",
            by_address={},
            notes=[
                f"制裁日期注册表不存在：{relative}；sanctions_public_timestamp 全部留空，"
                "不推断任何日期"
            ],
        )

    document = json.loads(path.read_text(encoding="utf-8"))
    source = document.get("source") or {}
    source_rel = str(source.get("path", "")).strip()
    expected = str(source.get("sha256", "")).strip()
    if not source_rel or not expected:
        raise SystemExit(
            f"{relative}: source.path / source.sha256 缺失，无法出具可复核的日期"
        )
    source_path = cfg.path(source_rel)
    if not source_path.exists():
        raise SystemExit(f"{relative}: 登记的日期源不存在：{source_rel}")
    actual = sha256_hex(source_path.read_bytes())
    if actual != expected:
        raise SystemExit(f"{relative}: 日期源摘要不符：{actual} != {expected}")

    basis = str(document.get("basis", "")).strip()
    precision = str(document.get("precision", "")).strip()
    if basis not in SANCTIONS_DATE_BASES:
        raise SystemExit(
            f"{relative}: basis={basis!r} 不在字典枚举内 {sorted(SANCTIONS_DATE_BASES)}"
        )
    if precision not in SANCTIONS_DATE_PRECISIONS:
        raise SystemExit(f"{relative}: precision={precision!r} 只能取 day")

    by_address: dict[str, dict] = {}
    conflicts: list[str] = []
    with source_path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            address = str(row.get("address", "")).strip().lower()
            if not (address.startswith("0x") and len(address) == 42):
                # 只接 Ethereum 地址；其他链的地址串不参与本数据集的匹配。
                continue
            date = normalise_date(row.get("entity_designation_date"))
            if not date:
                continue
            entry = {
                "date": date,
                "entity": str(row.get("entity", "")).strip(),
                "programs": str(row.get("ofac_programs", "")).strip(),
                "profile_id": str(row.get("sdn_profile_id", "")).strip(),
            }
            previous = by_address.get(address)
            if previous:
                # 同一地址可以同时挂在多个实体名下（共同钱包）；只有日期冲突才是
                # 数据问题，实体名不同不是。
                if previous["date"] != entry["date"]:
                    conflicts.append(address)
                elif entry["entity"] and entry["entity"] not in previous["entities"]:
                    previous["entities"].append(entry["entity"])
                continue
            by_address[address] = {
                **entry,
                "entities": [entry["entity"]] if entry["entity"] else [],
            }
    if conflicts:
        raise SystemExit(
            f"{relative}: 同一地址出现不同日期或实体，拒绝合并："
            f"{sorted(set(conflicts))[:5]}"
        )

    return SanctionsDates(
        path=relative,
        source_path=source_rel,
        sha256=expected,
        basis=basis,
        precision=precision,
        by_address=by_address,
        notes=[
            f"制裁指定日来源 {source_rel}（{len(by_address)} 个 Ethereum 地址，"
            f"实体级，sha256 {expected[:12]}…）"
        ],
    )


@dataclass
class MatchingRun:
    cfg: Config
    label_path: str = LABEL_PATH
    core_id: str = ""
    dictionary: dict[str, list[str]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.dictionary = load_dictionary(self.cfg.dictionary_csv)
        self.required = required_fields(self.cfg.dictionary_csv)
        self.ofac_index, self.ofac_manifest = load_ofac_index(self.cfg)
        self.sanctions = load_sanctions_dates(self.cfg)
        self.notes.extend(self.sanctions.notes)
        self.labels = load_labels(self.cfg, self.label_path)
        self.notes.extend(self.labels.notes)
        # 字典把 matched_at_utc 定义为"匹配执行时刻"，就写执行时刻；该字段不进
        # data_digest，复跑只改这一个字段，不改任何数据值。
        self.matched_at = now_utc()
        self.ofac_publish_date = normalise_date(self.ofac_manifest.get("publish_date"))

    # ------------------------------------------------------------- core bundle
    def core_dir(self) -> tuple[Path, dict]:
        """The ticket-05 bundle; located from its inputs, never written to."""
        return locate_core_bundle(self.cfg, self.core_id)

    # ------------------------------------------------------------------ layers
    def _row(self, event: dict, layer: str, source: str) -> dict:
        row = {field_name: "" for field_name in self.dictionary[MATCH_TABLE]}
        row.update(
            {
                "match_id": match_id(event["event_id"], layer, source),
                "event_id": event["event_id"],
                "match_layer": layer,
                "matched_at_utc": self.matched_at,
            }
        )
        return row

    def ofac_row(self, event: dict) -> dict:
        """One event against the pinned SDN snapshot, by exact address string."""
        address = event["affected_address"].lower()
        entry = self.ofac_index.get(address)
        dated = self.sanctions.date_for(address)
        entity_label = dated["entity"] if dated else ""
        if dated and len(dated.get("entities", [])) > 1:
            entity_label = f"{entity_label} 等 {len(dated['entities'])} 个实体"
        row = self._row(event, OFAC_LAYER, OFAC_SOURCE_NAME)
        row.update(
            {
                "match_source_name": OFAC_SOURCE_NAME,
                "match_source_url": OFAC_SOURCE_URL,
                "match_source_snapshot_sha256": self.ofac_manifest.get("csv_sha256", ""),
            }
        )
        if entry:
            row.update(
                {
                    "match_status": "matched",
                    "matched_entity_name": entry["name"],
                    "matched_record_id": entry["uid"],
                    "match_evidence_address_string": entry["address"],
                    "match_evidence_text": (
                        f"SDN uid={entry['uid']} type={entry.get('type', '')} "
                        f"program={entry.get('program', '')} "
                        f"currency={entry.get('currency', '')}"
                    ),
                    # 快照发布日期不是制裁日期：字典只允许它进 label_date。
                    "label_date": self.ofac_publish_date,
                    "label_date_basis": "list_snapshot_date",
                    "label_date_precision": "day",
                    "sanctions_public_timestamp": dated["date"] if dated else "",
                    "sanctions_date_basis": self.sanctions.basis if dated else "",
                    "sanctions_date_precision": self.sanctions.precision if dated else "",
                    "match_confidence": "exact_address",
                    "missing_reason_code": "" if dated else "label_date_missing",
                    "notes": (
                        (
                            "地址串精确匹配 SDN 快照；指定日取 SDN Advanced EntryEvent"
                            f"（实体级：{entity_label or 'n/a'}，"
                            f"profile {dated['profile_id'] or 'n/a'}，"
                            f"source_sha256 {self.sanctions.sha256[:12]}…），"
                            f"basis={self.sanctions.basis}、precision={self.sanctions.precision}。"
                            f"快照发布日 {self.ofac_publish_date or 'n/a'} 只记 label_date"
                            "（list_snapshot_date），不顶替指定日"
                        )
                        if dated
                        else (
                            "地址串精确匹配 SDN 快照；该地址不在日期提取源中"
                            f"（{self.sanctions.source_path or '未登记来源'}），"
                            "指定日留空并记 label_date_missing，不推断、不用快照日顶替"
                        )
                    ),
                }
            )
        else:
            row.update(
                {
                    "match_status": "unmatched",
                    "match_confidence": "none",
                    "missing_reason_code": "sanctions_record_missing",
                    "notes": (
                        "R6：SDN 快照地址集中没有该地址串；未匹配不等于未受制裁，"
                        "也不写 0 值或占位匹配"
                    ),
                }
            )
        return row

    def label_row(self, event: dict, labels: LabelRegistry | None = None) -> dict:
        """One event against dated historical exchange labels.

        Date rules, in order: no source -> ``not_attempted``; no label for the
        address -> ``not_applicable``; label dated after the event -> ``not_applicable``
        with the evidence kept (a later label must not explain an earlier event);
        undated label -> ``matched`` under R5 (sensitivity parameter only); otherwise
        ``matched`` with its date.
        """
        registry = labels if labels is not None else self.labels
        address = event["affected_address"].lower()
        label = registry.labels.get(address)
        source_name = str((label or {}).get("source_name") or "")
        row = self._row(event, LABEL_LAYER, source_name or "exchange_label_registry")

        if not registry.attempted:
            row.update(
                {
                    "match_status": "not_attempted",
                    "match_confidence": "none",
                    "match_source_name": "exchange_label_registry",
                    "match_source_snapshot_sha256": registry.sha256,
                    "missing_reason_code": "external_source_unavailable",
                    "notes": (
                        "没有带日期的历史交易所标签来源（config/exchange_labels.json 未登记 "
                        "sources）；not_attempted 不等于 unmatched，也不等于 0"
                    ),
                }
            )
            return row

        source = registry.source(source_name)
        row.update(
            {
                "match_source_name": source_name or "exchange_label_registry",
                "match_source_url": str(source.get("url", "")),
                "match_source_snapshot_sha256": str(source.get("snapshot_sha256", "")),
            }
        )
        if label is None:
            row.update(
                {
                    "match_status": "not_applicable",
                    "match_confidence": "none",
                    "missing_reason_code": "not_applicable",
                    "notes": (
                        "该地址不在已登记标签集内（标签集只覆盖交易所地址）；"
                        "不记为 matched，也不解释成零值"
                    ),
                }
            )
            return row

        label_date = label["label_date"]
        row.update(
            {
                "matched_entity_name": label["entity_name"],
                "matched_record_id": label["record_id"],
                "match_evidence_address_string": label["address"],
                "match_evidence_text": label["evidence_text"] or label["entity_name"],
                "label_date": label_date,
                "label_date_basis": label["label_date_basis"],
                "label_date_precision": "day" if label_date else "",
            }
        )
        if label_date and label_date > event_date(event):
            row.update(
                {
                    "match_status": "not_applicable",
                    "match_confidence": "none",
                    "missing_reason_code": "not_applicable",
                    "notes": (
                        f"标签日期 {label_date} 晚于事件时间 {event_date(event)}："
                        "这是事后标签，保留证据但不用于解释该事件（前视保护）"
                    ),
                }
            )
            return row
        row.update(
            {
                "match_status": "matched",
                "match_confidence": "exact_address",
                "missing_reason_code": "" if label_date else "label_date_missing",
                "notes": (
                    f"带日期标签（{label['label_date_basis']} {label_date}），"
                    "可用于带日期子集"
                    if label_date
                    else "R5：标签无日期，只参与 missing-label 敏感性，不进入带日期分析"
                ),
            }
        )
        return row

    def matches(self, events: list[dict]) -> list[dict]:
        rows: list[dict] = []
        for event in events:
            rows.append(self.ofac_row(event))
            rows.append(self.label_row(event))
        return rows

    # ------------------------------------------------------------ missingness
    def missingness(self, tables: dict[str, list[dict]]) -> list[dict]:
        """Field-level missing counts for every delivered table this module reports on."""
        rows: list[dict] = []
        for table, payload in tables.items():
            for field_name in self.dictionary[table]:
                missing = [row for row in payload if row[field_name] == ""]
                reasons = sorted(
                    {
                        row.get("missing_reason_code", "")
                        for row in missing
                        if row.get("missing_reason_code", "")
                    }
                )
                rows.append(
                    {
                        "table": table,
                        "field": field_name,
                        "required": (
                            "yes" if field_name in self.required.get(table, set()) else "no"
                        ),
                        "rows": str(len(payload)),
                        "missing": str(len(missing)),
                        "share": ratio(len(missing), len(payload)),
                        "reason_codes": ";".join(reasons),
                    }
                )
        return rows

    # ------------------------------------------------------------------ report
    def report(
        self,
        events: list[dict],
        matches: list[dict],
        core_manifest: dict,
        missingness: list[dict],
    ) -> dict:
        by_layer = {
            layer: [row for row in matches if row["match_layer"] == layer]
            for layer in LAYERS
        }
        event_index = {event["event_id"]: event for event in events}

        def rates(rows: list[dict], attempted_status: set[str]) -> dict:
            attempted = [row for row in rows if row["match_status"] in attempted_status]
            matched = [row for row in rows if row["match_status"] == "matched"]
            count = Counter(row["match_status"] for row in rows)
            return {
                "rows": len(rows),
                "status": dict(sorted(count.items())),
                "by_event": _rate(len(matched), len(attempted)),
            }

        ofac = rates(by_layer[OFAC_LAYER], {"matched", "unmatched"})
        labels = rates(by_layer[LABEL_LAYER], {"matched", "unmatched"})
        ofac_rows = by_layer[OFAC_LAYER]
        label_rows = by_layer[LABEL_LAYER]

        addresses = {event["affected_address"] for event in events}
        ofac_matched_addresses = {
            row["match_evidence_address_string"].lower()
            for row in ofac_rows
            if row["match_status"] == "matched"
        }

        def group(rows: list[dict], key: str) -> dict:
            """Rates per bucket; a bucket with nothing attempted reports ``null``."""
            buckets: dict[str, Counter] = {}
            for row in rows:
                event = event_index[row["event_id"]]
                bucket = buckets.setdefault(event[key], Counter())
                bucket["events"] += 1
                for status in ("matched", "unmatched", "not_applicable", "not_attempted"):
                    bucket[status] += int(row["match_status"] == status)
            output = {}
            for name, values in sorted(buckets.items()):
                attempted = values["matched"] + values["unmatched"]
                output[name] = {
                    "events": values["events"],
                    "attempted": attempted,
                    "matched": values["matched"],
                    "not_applicable": values["not_applicable"],
                    "not_attempted": values["not_attempted"],
                    "rate": (
                        ratio(values["matched"], attempted) if attempted else None
                    ),
                }
            return output

        dated_sanctions = [
            row
            for row in ofac_rows
            if row["match_status"] == "matched" and row["sanctions_public_timestamp"]
        ]
        dated_labels = [
            row
            for row in label_rows
            if row["match_status"] == "matched" and row["label_date"]
        ]
        undated_labels = [
            row
            for row in label_rows
            if row["match_status"] == "matched" and not row["label_date"]
        ]
        look_ahead = [
            row
            for row in label_rows
            if row["match_status"] == "not_applicable" and row["matched_record_id"]
        ]
        matched_any = {
            row["event_id"] for row in matches if row["match_status"] == "matched"
        }
        matched_addresses_any = {
            row["match_evidence_address_string"].lower()
            for row in matches
            if row["match_status"] == "matched"
        }
        matched_ofac = {
            row["event_id"] for row in ofac_rows if row["match_status"] == "matched"
        }
        matched_label = {
            row["event_id"] for row in label_rows if row["match_status"] == "matched"
        }
        matched_ofac_only = matched_ofac - matched_label
        matched_label_only = matched_label - matched_ofac
        matched_both = matched_ofac & matched_label

        return {
            "scope": {
                "core_id": core_manifest.get("core_id", ""),
                "core_data_digest": core_manifest["digests"]["data_digest"],
                "core_events": len(events),
                "observation_window": {
                    "start_block": core_manifest["scope"]["observation_start_block"],
                    "cutoff_block": core_manifest["scope"]["study_cutoff_block"],
                },
                "unique_addresses": len(addresses),
                "matches_table_rows": len(matches),
                # 分离的派生证据：交付事件表的资格分布与核心 manifest 一致，
                # 而不是靠一句"匹配不剔除"的自述。
                "eligibility_unchanged": dict(
                    sorted(Counter(event["analysis_eligibility"] for event in events).items())
                )
                == dict(
                    sorted(
                        core_manifest["metrics"]["core_set"][
                            "analysis_eligibility"
                        ].items()
                    )
                ),
            },
            "layers": {
                OFAC_LAYER: {
                    "source": OFAC_SOURCE_NAME,
                    "snapshot_sha256": self.ofac_manifest.get("csv_sha256", ""),
                    "snapshot_publish_date": self.ofac_publish_date,
                    "method": "地址串完全一致（大小写归一后比对），不做模糊匹配",
                    "attempted_status": ["matched", "unmatched"],
                },
                LABEL_LAYER: {
                    "registry": self.labels.path,
                    "registry_sha256": self.labels.sha256,
                    "sources": sorted(self.labels.sources),
                    "method": "带日期的历史标签注册表；无来源时记 not_attempted",
                    "attempted_status": ["matched", "unmatched"],
                },
            },
            "public_record_match_rates": {
                "ofac_sdn": {
                    **ofac,
                    # 匹配成功但缺可用的制裁日期，与"能进带日期分析"分开计数。
                    "matched_without_sanctions_date": ofac["by_event"]["numerator"]
                    - len(dated_sanctions),
                    "dated_by_event": _rate(
                        len(dated_sanctions), ofac["by_event"]["denominator"]
                    ),
                    "by_token": group(ofac_rows, "token_symbol"),
                    "by_event_class": group(ofac_rows, "event_class"),
                    "addresses": {
                        "total": len(addresses),
                        "matched": len(ofac_matched_addresses),
                        "rate": share(len(ofac_matched_addresses), len(addresses)),
                    },
                    "denominator": "核心集全部事件（含 R1/不可核验行）；匹配不参与剔除",
                },
                "exchange_label": {
                    **labels,
                    "matched_without_label_date": len(undated_labels),
                    "dated_by_event": _rate(
                        len(dated_labels), labels["by_event"]["denominator"]
                    ),
                    "by_token": group(label_rows, "token_symbol"),
                    "by_event_class": group(label_rows, "event_class"),
                    "denominator": "已登记标签来源覆盖的事件；未尝试的行不进分母",
                    "not_reportable_reason": (
                        "无带日期的历史交易所标签来源（external_source_unavailable）；"
                        "not_attempted 不计入任何分母"
                    ),
                },
                "any_layer": {
                    "events": len(matched_any),
                    "of_core_events": share(len(matched_any), len(events)),
                    "addresses": len(matched_addresses_any),
                    "of_unique_addresses": share(
                        len(matched_addresses_any), len(addresses)
                    ),
                    # 两层合并会掩盖"标签层 0 覆盖"，所以拆开写来源。
                    "composition": {
                        "ofac_only": len(matched_ofac_only),
                        "exchange_only": len(matched_label_only),
                        "both_layers": len(matched_both),
                    },
                },
            },
            "label_dates": {
                "ofac_sdn": {
                    "snapshot_publish_date": self.ofac_publish_date,
                    "label_date_basis": "list_snapshot_date",
                    "matched_rows_with_label_date": sum(
                        1 for row in ofac_rows if row["label_date"]
                    ),
                    "rows_with_sanctions_public_timestamp": len(dated_sanctions),
                    "sanctions_date_basis": self.sanctions.basis,
                    "sanctions_date_precision": self.sanctions.precision,
                    "sanctions_date_source": {
                        "registry": self.sanctions.path,
                        "path": self.sanctions.source_path,
                        "sha256": self.sanctions.sha256,
                    },
                    "matched_rows_without_sanctions_date": sum(
                        1
                        for row in ofac_rows
                        if row["match_status"] == "matched"
                        and not row["sanctions_public_timestamp"]
                    ),
                    "note": (
                        (
                            "指定日来自 SDN Advanced EntryEvent（实体级、precision=day）："
                            "同一实体的多个地址共享一个日期，不是地址级上榜日。"
                            "快照发布日只写 label_date，不顶替指定日；"
                            "不在日期源中的地址留空并记 label_date_missing，不进 timing-gap 子集"
                        )
                        if self.sanctions.registered
                        else "未登记制裁日期源：sanctions_public_timestamp 全部留空"
                    ),
                },
                "exchange_label": {
                    "sources_registered": len(self.labels.sources),
                    "labels_registered": len(self.labels.labels),
                    "labels_with_date": sum(
                        1 for label in self.labels.labels.values() if label["label_date"]
                    ),
                    "labels_without_date": sum(
                        1
                        for label in self.labels.labels.values()
                        if not label["label_date"]
                    ),
                    "dated_matched_rows": len(dated_labels),
                    "undated_matched_rows": len(undated_labels),
                    "look_ahead_rejected_rows": len(look_ahead),
                    "rule": (
                        "标签必须带日期；label_date 晚于事件时间记 not_applicable"
                        "（保留证据但不得用于解释该事件）；无日期只进 missing-label 敏感性"
                    ),
                },
            },
            "missing_values": {
                "fields": len(missingness),
                "fields_with_missing": sum(
                    1 for row in missingness if row["missing"] != "0"
                ),
                # 只有字典标 required=yes 的空值才叫缺口；其余空值按定义可为空。
                "required_field_gaps": [
                    f"{row['table']}.{row['field']}"
                    for row in missingness
                    if row["required"] == "yes" and row["missing"] != "0"
                ],
                "optional_fields_empty": [
                    f"{row['table']}.{row['field']}"
                    for row in missingness
                    if row["required"] == "no" and row["missing"] != "0"
                ],
                "by_table": {
                    table: {
                        "rows": next(
                            (
                                row["rows"]
                                for row in missingness
                                if row["table"] == table
                            ),
                            "0",
                        ),
                        "fields_with_missing": [
                            row["field"]
                            for row in missingness
                            if row["table"] == table and row["missing"] != "0"
                        ],
                    }
                    for table in sorted({row["table"] for row in missingness})
                },
                "matches_reason_codes": dict(
                    sorted(
                        Counter(
                            row["missing_reason_code"]
                            for row in matches
                            if row["missing_reason_code"]
                        ).items()
                    )
                ),
                "events_missingness_status": dict(
                    sorted(Counter(event["missingness_status"] for event in events).items())
                ),
                "events_reason_codes": dict(
                    sorted(
                        Counter(
                            event["missing_reason_code"]
                            for event in events
                            if event["missing_reason_code"]
                        ).items()
                    )
                ),
                "note": (
                    "空值与其原因码逐字段计数；required=yes 的空值必须带原因码（R1/R2 允许保留"
                    "空字段的占位行），未匹配、未尝试、不适用也都带原因码，"
                    "不会被折进 0 值或成功匹配"
                ),
            },
            "analysis_subsets": {
                "dated_sanctions": {
                    "events": len(dated_sanctions),
                    "rule": "match_status = matched 且 sanctions_public_timestamp 非空",
                    "reportable": bool(dated_sanctions),
                },
                "dated_exchange_label": {
                    "events": len(dated_labels),
                    "rule": "match_status = matched 且 label_date 不晚于事件时间",
                    "reportable": bool(dated_labels),
                },
                "note": (
                    "受限子集的规模由匹配证据与日期充分性决定；timing gap 与 outflow "
                    "属工单 08，本工单只登记子集大小"
                ),
            },
            "coverage_statement": (
                "匹配只在核心集内部进行：OFAC 层是当前快照的地址串比对，"
                "交易所标签层读带日期的注册表。"
                f"OFAC 命中 {len(ofac_matched_addresses)} 个地址 / {len(addresses)} 个地址"
                f"（{share(len(ofac_matched_addresses), len(addresses))}），"
                f"其中 {len(dated_sanctions)} 个事件带实体级指定日"
                f"（{len(dated_sanctions)} / {len(ofac_rows)} 行 OFAC 层），"
                f"交易所标签 {len(dated_labels)} 行带可用日期。"
                "匹配覆盖只占冻结活动的一小部分，"
                "任何基于匹配子集的结论不得外推到全部冻结活动。"
            ),
            "caveats": {
                "matches_do_not_filter": (
                    "matches 与核心事件表分离保存：匹配结果不进入 "
                    "events.analysis_eligibility，核心集事件数不变，匹配导致的剔除为 0"
                ),
                "unmatched_is_not_absence_of_sanctions": (
                    "unmatched / not_attempted / not_applicable 都不等于未受制裁，"
                    "也不等于成功匹配"
                ),
                "missingness_is_not_zero": (
                    "缺失、未匹配、未尝试一律留空并带 missing_reason_code，"
                    "不填 0、不计入任何比率的分子"
                ),
                "snapshot_postdates_events": (
                    "OFAC 层是单一快照（publish "
                    f"{self.ofac_publish_date or 'n/a'}）的地址串比对，快照晚于全部事件："
                    "匹配只说明该地址出现在当前清单上，不构成冻结时已被指定的证据"
                ),
                "label_dates_are_load_bearing": (
                    "交易所以外标签必须带日期；标签日期晚于事件时间的不用于该事件，"
                    "无日期的标签不进入带日期分析"
                ),
                "coverage_not_extrapolable": (
                    "匹配覆盖可能只占事件的一小部分；结论只在匹配子集内成立，"
                    "不外推到全部冻结活动"
                ),
                "private_request_not_observable": (
                    "私有执法请求时间不可观测（not_observable），无对应字段，"
                    "不参与任何指标，也不由公开时间差推断"
                ),
                "rates_are_not_issuer_comparison": (
                    "分代币/分类别的匹配率只是同一份匹配分母的分解，"
                    "不是 USDT 与 USDC 的行为比较；同口径比较属工单 07"
                ),
            },
            "rule_checks": self.rules_self_test(),
        }

    # --------------------------------------------------------------- rule test
    def rules_self_test(self) -> dict:
        """Assert the date/absence rules on fixtures; returns ``{rule: status}``.

        The delivered registry is empty, so without these assertions the look-ahead
        and undated-label guards would be prose that no data exercises. They run the
        same functions that build the delivered rows, so the seam stays at the output.
        """
        results: dict[str, str] = {}
        fixture = {
            "event_id": "ev_rulecheck",
            "affected_address": "0x" + "11" * 20,
            "block_time_utc": "2021-06-01T00:00:00Z",
            "token_symbol": "USDT",
            "event_class": "freezing",
        }

        def registry(label: dict | None, sources: dict | None = None) -> LabelRegistry:
            return LabelRegistry(
                path="<rule-check>",
                sha256="",
                sources={"fixture": {"name": "fixture"}} if sources is None else sources,
                labels={fixture["affected_address"]: label} if label else {},
            )

        dated = {
            "address": fixture["affected_address"],
            "entity_name": "FIXTURE EXCHANGE",
            "record_id": "L1",
            "label_date": "2021-01-01",
            "label_date_basis": "label_publication_date",
            "evidence_text": "fixture",
            "source_name": "fixture",
        }
        before = self.label_row(fixture, registry(dated))
        results["dated_label_before_event"] = (
            "ok"
            if before["match_status"] == "matched"
            and before["label_date"] == "2021-01-01"
            and before["match_confidence"] == "exact_address"
            else "failed"
        )

        after = dict(dated, label_date="2022-01-01")
        rejected = self.label_row(fixture, registry(after))
        results["label_after_event_not_used"] = (
            "ok"
            if rejected["match_status"] == "not_applicable"
            and rejected["matched_record_id"]
            and rejected["missing_reason_code"] == "not_applicable"
            else "failed"
        )

        undated = dict(dated, label_date="", label_date_basis="")
        kept = self.label_row(fixture, registry(undated))
        results["undated_label_kept_as_r5"] = (
            "ok"
            if kept["match_status"] == "matched"
            and not kept["label_date"]
            and kept["missing_reason_code"] == "label_date_missing"
            else "failed"
        )

        none = self.label_row(fixture, registry({}, sources={}))
        results["no_source_is_not_attempted"] = (
            "ok"
            if none["match_status"] == "not_attempted"
            and none["match_confidence"] == "none"
            and none["missing_reason_code"] == "external_source_unavailable"
            else "failed"
        )

        unmatched_event = dict(fixture, affected_address="0x" + "00" * 20)
        ofac = self.ofac_row(unmatched_event)
        results["ofac_unmatched_is_not_a_zero"] = (
            "ok"
            if ofac["match_status"] == "unmatched"
            and not ofac["matched_record_id"]
            and not ofac["sanctions_public_timestamp"]
            and ofac["missing_reason_code"] == "sanctions_record_missing"
            else "failed"
        )

        # 指定日必须来自登记源：把清单发布日当指定日要能被抓出（R6）。
        probe = self._row({"event_id": "ev_date_probe"}, OFAC_LAYER, OFAC_SOURCE_NAME)
        probe.update(
            {
                "match_status": "matched",
                "match_evidence_address_string": "0x" + "11" * 20,
                "sanctions_public_timestamp": "2026-11-09",
                "sanctions_date_basis": "list_snapshot_date",
                "sanctions_date_precision": "day",
                "missing_reason_code": "",
            }
        )
        results["snapshot_date_is_not_a_sanctions_date"] = (
            "ok"
            if any("不在枚举内" in problem for problem in self._date_problems(probe))
            else "failed"
        )

        # 反过来，一条合法日期必须能追回登记源，否则规则会变成只拒绝、不核对。
        sample = next(iter(self.sanctions.by_address.items()), None)
        if sample is not None:
            address, entry = sample
            traced = self._row({"event_id": "ev_date_probe"}, OFAC_LAYER, OFAC_SOURCE_NAME)
            traced.update(
                {
                    "match_status": "matched",
                    "match_evidence_address_string": address,
                    "sanctions_public_timestamp": entry["date"],
                    "sanctions_date_basis": self.sanctions.basis,
                    "sanctions_date_precision": self.sanctions.precision,
                    "missing_reason_code": "",
                }
            )
            results["dated_row_traces_back_to_registered_source"] = (
                "ok" if not self._date_problems(traced) else "failed"
            )
        return results

    # ------------------------------------------------------------------ runner
    def run(self) -> dict:
        out_dir, core_manifest = self.core_dir()
        tables = {
            table: read_table(out_dir / f"{table}.csv") for table in CORE_TABLE_ORDER
        }
        for table, rows in tables.items():
            # 缺列时直接报出字段名，而不是在后面某处抛 KeyError。
            assert_columns(table, self.dictionary[table], rows)
        events = tables["events"]
        matches = self.matches(events)
        missingness = self.missingness(
            {"events": events, "balances": tables["balances"], MATCH_TABLE: matches}
        )
        report = self.report(events, matches, core_manifest, missingness)
        failed = {
            rule: status
            for rule, status in report["rule_checks"].items()
            if status != "ok"
        }
        if failed:
            raise SystemExit(f"匹配规则自检失败：{sorted(failed)}")
        return {
            "matches": matches,
            "missingness": missingness,
            "report": report,
            "core_dir": out_dir,
            "core_manifest": core_manifest,
            "events": events,
        }

    # --------------------------------------------------------------- delivery
    def run_id(self, core_manifest: dict) -> str:
        return "match_" + sha256_hex(
            canonical_json(
                {
                    "core_data_digest": core_manifest["digests"]["data_digest"],
                    "ofac_snapshot_sha256": self.ofac_manifest.get("csv_sha256", ""),
                    "ofac_publish_date": self.ofac_publish_date,
                    "sanctions_dates_sha256": self.sanctions.sha256,
                    "sanctions_date_basis": self.sanctions.basis,
                    "sanctions_date_precision": self.sanctions.precision,
                    "labels_sha256": self.labels.sha256,
                    "dictionary_sha256": sha256_hex(
                        self.cfg.dictionary_csv.read_bytes()
                    ),
                    "dataset_contract_version": self.cfg.registry[
                        "dataset_contract_version"
                    ],
                }
            ).encode()
        )[:32]

    def deliver(self, outcome: dict) -> dict:
        matches = outcome["matches"]
        assert_columns(MATCH_TABLE, self.dictionary[MATCH_TABLE], matches)

        run_id = self.run_id(outcome["core_manifest"])
        out_dir = self.cfg.out_root / run_id
        out_dir.mkdir(parents=True, exist_ok=True)

        matches_csv = out_dir / "matches.csv"
        write_csv(matches_csv, self.dictionary[MATCH_TABLE], matches)
        matches_parquet = write_parquet(
            out_dir / "matches.parquet", self.dictionary[MATCH_TABLE], matches
        )

        missingness_fields = [
            "table",
            "field",
            "required",
            "rows",
            "missing",
            "share",
            "reason_codes",
        ]
        missingness_csv = out_dir / "missingness.csv"
        write_csv(missingness_csv, missingness_fields, outcome["missingness"])
        missingness_parquet = write_parquet(
            out_dir / "missingness.parquet", missingness_fields, outcome["missingness"]
        )

        report_path = out_dir / "report.json"
        report_path.write_text(
            json.dumps(outcome["report"], indent=1, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        manifest = {
            # 批次 id 不叫 match_id：那是 matches 表的主键，同名会让复核者读错。
            "match_batch_id": run_id,
            "dataset_contract_version": self.cfg.registry["dataset_contract_version"],
            "generated_at_utc": now_utc(),
            "inputs": {
                "core_id": outcome["core_manifest"]["core_id"],
                "core_dir": self.cfg.rel(outcome["core_dir"]),
                "core_data_digest": outcome["core_manifest"]["digests"]["data_digest"],
                "ofac_snapshot": self.cfg.rel(self.cfg.ofac_dir / "SDN.csv"),
                "ofac_snapshot_sha256": self.ofac_manifest.get("csv_sha256", ""),
                "ofac_publish_date": self.ofac_publish_date,
                "label_registry": self.labels.path,
                "label_registry_sha256": self.labels.sha256,
                "sanctions_dates_registry": self.sanctions.path,
                "sanctions_dates_source": self.sanctions.source_path,
                "sanctions_dates_sha256": self.sanctions.sha256,
                "sanctions_date_basis": self.sanctions.basis,
                "sanctions_date_precision": self.sanctions.precision,
                "dictionary": self.cfg.rel(self.cfg.dictionary_csv),
            },
            "counts": {
                "matches": len(matches),
                "by_layer": dict(
                    sorted(Counter(row["match_layer"] for row in matches).items())
                ),
                "by_status": dict(
                    sorted(Counter(row["match_status"] for row in matches).items())
                ),
                "core_events": len(outcome["events"]),
                "missingness_fields": len(outcome["missingness"]),
            },
            "separation": {
                "core_tables_written_by_this_step": [],
                "match_based_exclusions": 0,
                "core_events_changed": 0,
                "eligibility_unchanged": outcome["report"]["scope"][
                    "eligibility_unchanged"
                ],
                "note": (
                    "匹配只读核心集；matches 写入独立目录，不改动 events/balances/validation。"
                    "match_based_exclusions 恒为 0 与 eligibility_unchanged 一起才成立："
                    "后者由交付事件表与核心 manifest 的 analysis_eligibility 计数比对得出"
                ),
            },
            # 指标只在 report.json 里落一份：manifest 指向它，不复制数字，
            # 免得两处数字各自漂移。
            "report": {
                "path": self.cfg.rel(report_path),
                "objectives": list(OBJECTIVES),
            },
            "checks": {
                "columns": "matches 写出前按 docs/data-dictionary.csv 断言",
                "rules": outcome["report"]["rule_checks"],
                "offline_recheck": "python scripts/reconstruct_matches.py --check",
            },
            "columns_verified": True,
            "parquet_written": {
                "matches": matches_parquet,
                "missingness": missingness_parquet,
            },
            "digests": {
                "data_digest": data_digest({MATCH_TABLE: matches}),
                "artifact_digest": artifact_digest(
                    [matches_csv, missingness_csv, report_path]
                ),
                "data_digest_excludes": {
                    MATCH_TABLE: sorted(PROVENANCE_TIME_FIELDS.get(MATCH_TABLE, set()))
                },
            },
            "notes": self.notes,
            "reproduce_command": "python scripts/reconstruct_matches.py",
            "outputs": {
                "matches": self.cfg.rel(matches_csv),
                "missingness": self.cfg.rel(missingness_csv),
                "report": self.cfg.rel(report_path),
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
                # 同一输入的复跑不改写取证时刻。
                manifest["generated_at_utc"] = previous["generated_at_utc"]
        manifest["last_verified_at_utc"] = now_utc()
        manifest_path.write_text(
            json.dumps(manifest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        manifest["manifest_path"] = self.cfg.rel(manifest_path)
        return manifest

    # ------------------------------------------------------------------ check
    def _date_problems(self, row: dict) -> list[str]:
        """Rules that make a sanctions date usable: basis, precision, provenance.

        Kept as one function so the delivered table, ``--check`` and the synthetic
        rule test all exercise the same seam. A date that cannot be traced back to
        the registered extract is an error, not a softer confidence level.
        """
        problems: list[str] = []
        stamp = row["sanctions_public_timestamp"]
        if not stamp:
            if row["sanctions_date_basis"] or row["sanctions_date_precision"]:
                problems.append(f"{row['match_id']} 有日期口径但没有指定日")
            if (
                row["match_status"] == "matched"
                and self.sanctions.registered
                and row["missing_reason_code"] != "label_date_missing"
            ):
                problems.append(
                    f"{row['match_id']} 无指定日却没有 label_date_missing 原因码"
                )
            return problems

        if row["match_status"] != "matched":
            problems.append(f"{row['match_id']} 未匹配却写了指定日")
        if row["sanctions_date_basis"] not in SANCTIONS_DATE_BASES:
            problems.append(
                f"{row['match_id']} 的指定日口径 {row['sanctions_date_basis']!r} "
                f"不在枚举内（快照发布日不是指定日）"
            )
        if row["sanctions_date_precision"] not in SANCTIONS_DATE_PRECISIONS:
            problems.append(
                f"{row['match_id']} 的指定日精度 {row['sanctions_date_precision']!r} 不是 day"
            )
        dated = self.sanctions.date_for(row["match_evidence_address_string"])
        if dated is None:
            problems.append(f"{row['match_id']} 写了指定日但地址不在登记的日期源中")
        elif dated["date"] != stamp:
            problems.append(
                f"{row['match_id']} 的指定日与日期源不一致：{stamp} != {dated['date']}"
            )
        if row["missing_reason_code"]:
            problems.append(
                f"{row['match_id']} 有指定日却仍带缺失原因码 "
                f"{row['missing_reason_code']!r}"
            )
        return problems

    def check(self) -> list[str]:
        """Offline re-check of a delivered match bundle (no network, no writes).

        Rebuilds the match table from the core events + the pinned OFAC snapshot +
        the label registry and compares it row by row, re-derives the report and the
        missingness view, re-runs the rule self-test, and asserts the invariants that
        make the layer honest: nothing filtered, every claim carries evidence, and no
        absent value is dressed up as a match or a zero.
        """
        out_dir, core_manifest = self.core_dir()
        run_id = self.run_id(core_manifest)
        match_dir = self.cfg.out_root / run_id
        manifest_path = match_dir / "manifest.json"
        if not manifest_path.exists():
            return [f"匹配层产物缺失：{self.cfg.rel(manifest_path)}"]

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        problems: list[str] = []

        matches = read_table(match_dir / "matches.csv")
        try:
            assert_columns(MATCH_TABLE, self.dictionary[MATCH_TABLE], matches)
        except ValueError as exc:
            problems.append(str(exc))
            return problems
        if data_digest({MATCH_TABLE: matches}) != manifest["digests"]["data_digest"]:
            problems.append("matches.csv 重算的数据摘要与 manifest 不一致")

        missingness = read_table(match_dir / "missingness.csv")
        report = json.loads((match_dir / "report.json").read_text(encoding="utf-8"))

        # 1. separation: the core bundle must be byte-identical to its own manifest.
        core_tables = {
            table: read_table(out_dir / f"{table}.csv") for table in CORE_TABLE_ORDER
        }
        for table, rows in core_tables.items():
            try:
                assert_columns(table, self.dictionary[table], rows)
            except ValueError as exc:
                problems.append(f"核心表 {table}: {exc}")
        if data_digest(core_tables) != core_manifest["digests"]["data_digest"]:
            problems.append("核心集表被改动：匹配层不允许写回 events/balances/validation")

        events = core_tables["events"]
        expected = self.matches(events)
        if len(expected) != len(matches):
            problems.append(
                f"matches 行数与重建结果不一致：{len(matches)} != {len(expected)}"
            )
        delivered = {row["match_id"]: row for row in matches}
        for row in expected:
            current = delivered.pop(row["match_id"], None)
            if current is None:
                problems.append(f"交付表缺少 match_id={row['match_id']}")
                continue
            diff = {
                key: (row[key], current[key])
                for key in row
                if key != "matched_at_utc" and row[key] != current[key]
            }
            if diff:
                problems.append(f"match_id={row['match_id']} 与重建结果不一致：{diff}")
        for leftover in delivered:
            problems.append(f"交付表多出未重建的 match_id={leftover}")

        # 2. both layers, exactly one row per event, and the core set untouched.
        covered = Counter(row["event_id"] for row in matches)
        if set(covered) != {event["event_id"] for event in events}:
            problems.append("matches 覆盖的事件集合与核心集事件集合不一致")
        for event_id, count in covered.items():
            if count != len(LAYERS):
                problems.append(f"{event_id} 的匹配行数为 {count}，应为 {len(LAYERS)}")

        # 3. per-row evidence and absence rules.
        event_index = {event["event_id"]: event for event in events}
        for row in matches:
            # 空原因码表示"没有缺失"：只有带指定日的匹配行可以这样；其余状态
            # 的原因码在下面的分支里单独要求。
            if row["missing_reason_code"] and row["missing_reason_code"] not in MATCH_REASONS:
                problems.append(
                    f"{row['match_id']} 的 missing_reason_code={row['missing_reason_code']!r} 不在字典枚举内"
                )
            if row["match_status"] == "matched":
                if row["match_confidence"] != "exact_address":
                    problems.append(f"{row['match_id']} 匹配成功但置信度不是 exact_address")
                if not row["match_evidence_address_string"]:
                    problems.append(f"{row['match_id']} 匹配成功但没有证据地址串")
                if not row["matched_record_id"]:
                    problems.append(f"{row['match_id']} 匹配成功但没有记录 ID")
                if not row["match_source_snapshot_sha256"]:
                    problems.append(f"{row['match_id']} 匹配成功但没有来源快照摘要")
                if (
                    row["match_layer"] == LABEL_LAYER
                    and row["label_date"]
                    and row["label_date"] > event_date(event_index[row["event_id"]])
                ):
                    problems.append(
                        f"{row['match_id']} 用晚于事件的标签日期声称匹配（前视）"
                    )
            elif row["match_status"] == "not_applicable" and row["matched_record_id"]:
                # 前视保护：标签存在但晚于事件，证据保留、状态不成立。
                if not row["matched_entity_name"]:
                    problems.append(
                        f"{row['match_id']} 判为 not_applicable 却只留了部分证据"
                    )
            elif row["matched_entity_name"] or row["match_evidence_address_string"]:
                problems.append(
                    f"{row['match_id']} 状态为 {row['match_status']} 却带匹配证据"
                )
            if row["match_status"] in {"unmatched", "not_attempted", "not_applicable"}:
                if not row["missing_reason_code"]:
                    problems.append(
                        f"{row['match_id']} 状态为 {row['match_status']} 却没有原因码"
                    )
            if row["match_layer"] == OFAC_LAYER:
                # 指定日必须能追回登记源；快照发布日不能顶替它（R6）。
                problems.extend(self._date_problems(row))
                if row["match_status"] == "matched" and row["label_date_basis"] != "list_snapshot_date":
                    problems.append(
                        f"{row['match_id']} 的 OFAC 标签日期口径不是 list_snapshot_date"
                    )
            if row["sanctions_public_timestamp"] and not row["sanctions_date_basis"]:
                problems.append(f"{row['match_id']} 有制裁日期但没有日期口径")

        # 4. the report and the missingness view must be re-derivable.
        recomputed_report = self.report(events, expected, core_manifest, missingness)
        recomputed_missingness = self.missingness(
            {"events": events, "balances": core_tables["balances"], MATCH_TABLE: expected}
        )
        if missingness != recomputed_missingness:
            problems.append("missingness.csv 与按核心表重算的缺失统计不一致")
        for key, value in recomputed_report.items():
            if report.get(key) != value:
                problems.append(f"report.json 的 {key} 与重算结果不一致")
        for rule, status in recomputed_report["rule_checks"].items():
            if status != "ok":
                problems.append(f"规则自检失败：{rule}")
        for caveat in REQUIRED_CAVEATS:
            if not report.get("caveats", {}).get(caveat):
                problems.append(f"report.json 缺少陈述：{caveat}")

        # 5. no rate is invented when nothing was attempted, and no required field
        #    is empty without a reason code.
        for layer in LAYERS:
            rates = report["public_record_match_rates"][layer]
            by_event = rates["by_event"]
            if by_event["denominator"] == 0 and by_event["rate"] is not None:
                problems.append(f"{layer} 没有尝试匹配却报告了比率")
            if by_event["numerator"] > by_event["denominator"]:
                problems.append(f"{layer} 的匹配分子大于分母")
            if rates["status"].get("not_attempted", 0) and by_event["denominator"]:
                problems.append(f"{layer} 把未尝试的行计入了匹配分母")
            for bucket, values in {**rates["by_token"], **rates["by_event_class"]}.items():
                if values["attempted"] == 0 and values["rate"] is not None:
                    problems.append(f"{layer}/{bucket} 没有尝试却报告了比率")

        # 必填字段可以为空（R1/R2 保留占位行），但必须带原因码；不带原因码才是违规。
        for row in missingness:
            if (
                row["required"] == "yes"
                and row["missing"] != "0"
                and not row["reason_codes"]
            ):
                problems.append(
                    f"{row['table']}.{row['field']} 有必填字段缺失但没有原因码"
                )

        if manifest["separation"]["eligibility_unchanged"] is not True:
            problems.append("交付事件表的资格分布与核心 manifest 不一致")

        report_path = self.cfg.path(manifest["report"]["path"])
        if report_path != match_dir / "report.json":
            problems.append("manifest.report.path 未指向本批次的 report.json")
        if manifest.get("match_batch_id") != run_id:
            problems.append(
                f"manifest.match_batch_id={manifest.get('match_batch_id')} 与输入派生的 {run_id} 不一致"
            )

        if manifest["separation"]["match_based_exclusions"] != 0:
            problems.append("manifest 声明存在按匹配结果剔除的事件")
        return problems
