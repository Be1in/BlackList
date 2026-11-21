"""Build and verify the dataset manifest (checksums, row counts, batch ids).

Usage:
    python scripts/build_dataset_manifest.py            # write dataset_manifest.json
    python scripts/build_dataset_manifest.py --check    # verify files against it
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "dataset_manifest.json"
CORE = "data/recon/core_64c8c7f9452291663926113a2b1ecc20"
MATCH = "data/recon/match_e7a32e2a4120dccb5d5cfe1edf360766"
COMPARE = "data/recon/compare_9f973ff43a20c53a9fdf9bfac13675be"
RESTRICT = "data/recon/restrict_4c003f027d634313a28140234c40a510"
MATERIALS = "data/recon/materials_fe2495c8b40a1d848c29802a422bbb22"

FILES = [
    ("events", f"{CORE}/events.csv", "核心事件表：一条日志一行"),
    ("events_parquet", f"{CORE}/events.parquet", "核心事件表 Parquet 副本"),
    ("balances", f"{CORE}/balances.csv", "D1 / at_event / D2 余额"),
    ("balances_parquet", f"{CORE}/balances.parquet", "余额 Parquet 副本"),
    ("validation", f"{CORE}/validation.csv", "21,492 条核对"),
    ("validation_parquet", f"{CORE}/validation.parquet", "核对 Parquet 副本"),
    ("chain_registry", f"{CORE}/chain_registry.csv", "链登记"),
    ("coverage_gaps", f"{CORE}/coverage_gaps.csv", "覆盖差集（0 行）"),
    ("scope_excluded", f"{CORE}/scope_excluded.csv", "观察期外 90 条事件"),
    ("core_manifest", f"{CORE}/manifest.json", "核心批次清单与摘要"),
    ("matches", f"{MATCH}/matches.csv", "每事件 × 2 层匹配结果"),
    ("missingness", f"{MATCH}/missingness.csv", "逐字段缺失"),
    ("match_manifest", f"{MATCH}/manifest.json", "匹配批次清单"),
    ("comparison", f"{COMPARE}/comparison.csv", "19 指标 × 2 issuer"),
    ("differences", f"{COMPARE}/differences.csv", "19 行差值"),
    ("sensitivity", f"{COMPARE}/sensitivity.csv", "26 行敏感性"),
    ("restricted_timing", f"{RESTRICT}/timing_gaps.csv", "12 行 timing gap"),
    ("restricted_outflows", f"{RESTRICT}/exchange_outflows.csv", "6 行 outflow"),
    ("restricted_balances", f"{RESTRICT}/frozen_balances.csv", "8 行受限余额"),
    ("materials_index", f"{MATERIALS}/materials_index.csv", "四项支撑材料索引"),
    ("field_availability", f"{MATERIALS}/field_availability.csv", "116 字段可得性"),
    ("metric_coverage", f"{MATERIALS}/metric_coverage.csv", "106 指标覆盖"),
    ("wording_checks", f"{MATERIALS}/wording_checks.csv", "32 行措辞核对"),
    ("chain_scope", f"{MATERIALS}/chain_scope.csv", "链范围与待确认项"),
    ("sample_events", "data/samples/sample_events.csv", "小样本事件"),
    ("sample_balances", "data/samples/sample_balances.csv", "小样本余额"),
    ("sample_matches", "data/samples/sample_matches.csv", "小样本匹配"),
    ("sample_validation", "data/samples/sample_validation.csv", "小样本核对"),
    ("dictionary_md", "docs/data-dictionary.md", "数据字典"),
    ("dictionary_csv", "docs/data-dictionary.csv", "数据字典机器可读副本"),
    ("definitions", "docs/definitions.md", "事件与余额定义契约"),
    ("validation_table", "docs/validation-table.md", "验证表协议"),
    ("known_issues", "docs/known-issues.md", "已登记缺口与验证入口"),
    ("audit_report", "docs/dataset-audit.md", "契约审计报告"),
    ("manual_signoff_doc", "docs/manual-signoff.md", "人工签署说明"),
    ("manual_signoff_sample", "data/derived/manual_signoff_sample.csv", "26 行抽样签署表"),
    ("dataset_card", "DATASET.md", "数据说明（datasheet）"),
    ("glossary", "GLOSSARY.md", "术语表"),
    ("adr_designation_dates", "docs/adr/0002-entity-level-designation-dates.md", "指定日接入决策记录"),
    ("d2_population_full", "data/derived/d2_population_full.csv", "D2 全量总体（3,573）"),
    ("event_state_corrections", "data/derived/event_state_corrections.csv", "16 行状态修正"),
    ("cutoff_gap", "data/derived/cutoff_blacklist_gap.json", "29 地址缺口证据"),
    ("audit", "data/derived/dataset_audit.json", "67 项审计结果"),
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def rows_of(path: Path) -> int | None:
    if path.suffix.lower() != ".csv":
        return None
    with path.open(encoding="utf-8", newline="") as fh:
        return max(0, sum(1 for _ in fh) - 1)


def build() -> dict:
    entries = []
    for key, rel, role in FILES:
        path = ROOT / rel
        if not path.exists():
            raise SystemExit(f"missing file: {rel}")
        entries.append(
            {
                "key": key,
                "path": rel,
                "role": role,
                "bytes": path.stat().st_size,
                "rows": rows_of(path),
                "sha256": sha256(path),
            }
        )

    manifests = {}
    for key, rel in (
        ("core", f"{CORE}/manifest.json"),
        ("match", f"{MATCH}/manifest.json"),
        ("compare", f"{COMPARE}/manifest.json"),
        ("restrict", f"{RESTRICT}/manifest.json"),
        ("materials", f"{MATERIALS}/manifest.json"),
    ):
        data = json.loads((ROOT / rel).read_text(encoding="utf-8"))
        manifests[key] = {
            "path": rel,
            "batch_id": (
                data.get("core_id")
                or data.get("match_batch_id")
                or data.get("compare_batch_id")
                or data.get("restrict_batch_id")
                or data.get("package_id")
                or data.get("batch_id")
                or ""
            ),
            "data_digest": data.get("digests", {}).get("data_digest", ""),
            "artifact_digest": data.get("digests", {}).get("artifact_digest", ""),
        }

    audit = json.loads((ROOT / "data/derived/dataset_audit.json").read_text(encoding="utf-8"))
    payload = {
        "dataset": "Observable Stablecoin Freeze Events (Ethereum, USDT/USDC)",
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scope": {
            "chain": "ethereum",
            "chain_id": 1,
            "tokens": ["USDT", "USDC"],
            "observation_start_block": 10274701,
            "study_cutoff_block": 26404630,
            "events": 5970,
            "balances": 9466,
            "validation": 21492,
            "d2_population": 3573,
        },
        "batches": manifests,
        "audit_summary": audit["summary"],
        "files": entries,
        "known_issues": [],
    }
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    if args.check:
        if not OUT.exists():
            raise SystemExit("dataset_manifest.json missing; run without --check first")
        expected = json.loads(OUT.read_text(encoding="utf-8"))
        problems = []
        for entry in expected["files"]:
            path = ROOT / entry["path"]
            if not path.exists():
                problems.append(f"missing: {entry['path']}")
                continue
            if sha256(path) != entry["sha256"]:
                problems.append(f"sha256 changed: {entry['path']}")
            if rows_of(path) != entry["rows"]:
                problems.append(f"row count changed: {entry['path']}")
        if problems:
            print("dataset manifest check: FAIL")
            for problem in problems:
                print(" -", problem)
            raise SystemExit(4)
        print(f"dataset manifest check: ok（{len(expected['files'])} files）")
        return

    payload = build()
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT.name}: {len(payload['files'])} files")
    print(json.dumps(payload["audit_summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
