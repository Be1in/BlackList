"""Package the supporting materials and audit the descriptive wording (ticket 09).
Reads the delivered core set (ticket 05), match layer (ticket 06), issuer comparison
(ticket 07) and restricted-subset analyses (ticket 08), plus the small sample, the data
dictionary, the scripts and the validation tables, and writes a separate package:

* ``materials_index.csv``: one row per component with path, digest, offline check command
  and what that command proves, so fields and definitions can be checked without
  re-running the full collection;
* ``field_availability.csv``: one row per dictionary field with its availability tier
  (``A`` consistently available for both issuers / ``B`` conditional external source) and
  the delivered non-null counts per issuer;
* ``metric_coverage.csv``: one row per delivered metric with its coverage scope, its
  reporting scope (all events vs supported subset) and ``causal_inference_permitted = no``;
* ``wording_checks.csv``: every descriptive-wording constraint checked against structured
  evidence, per material, with the observed value and the pass/fail;
* ``chain_scope.csv``: the chain registry with the pending chain-scope decision and what
  confirming it would change about collection;
* ``report.json``: counts, conclusions, caveats and the guard self-test;
* ``manifest.json``: inputs, digests and counts.

    python scripts/reconstruct_materials.py
    python scripts/reconstruct_materials.py --check
    python scripts/reconstruct_materials.py --expect-data-digest <hex>

No network access is needed: every input is a delivered snapshot on disk.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from recon.config import load_config  # noqa: E402
from recon.materials import MaterialsRun  # noqa: E402

EXIT_RUN_ERROR = 1
EXIT_DIGEST_MISMATCH = 2
EXIT_CHECK_FAILED = 4


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--core-id",
        default="",
        help="核心集批次目录名；缺省按输入派生（不猜最新目录）",
    )
    parser.add_argument("--match-id", default="", help="匹配层批次目录名；缺省按上游摘要派生")
    parser.add_argument("--compare-id", default="", help="比较批次目录名；缺省按上游摘要派生")
    parser.add_argument("--restrict-id", default="", help="受限子样本批次目录名；缺省按上游摘要派生")
    parser.add_argument("--dictionary", default="docs/data-dictionary.csv")
    parser.add_argument("--out-root", default="data/recon")
    parser.add_argument(
        "--expect-data-digest",
        default=None,
        help="与支撑材料包的数据摘要比对；不一致时退出码 2",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="只复核已交付产物：重算五张表、报告与规则自检，不访问网络、不写盘",
    )
    parser.add_argument("--json", action="store_true", help="打印完整 manifest")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cfg = load_config(
        dictionary_csv=args.dictionary,
        out_root=args.out_root,
    )
    run = MaterialsRun(
        cfg=cfg,
        match_id=args.match_id,
        compare_id=args.compare_id,
        restrict_id=args.restrict_id,
    )

    if args.check:
        problems = run.check()
        if problems:
            for problem in problems:
                print(f"materials check: {problem}", file=sys.stderr)
            return EXIT_CHECK_FAILED
        print(
            "materials check  : ok（四项材料齐备、字段可得性可重建、指标覆盖范围一致、"
            "措辞核对全部通过、规则自检通过）"
        )
        return 0

    try:
        outcome = run.run()
        manifest = run.deliver(outcome)
    except (SystemExit, ValueError, KeyError, OSError) as exc:
        print(f"运行失败：{exc}", file=sys.stderr)
        return EXIT_RUN_ERROR

    report = outcome["report"]
    if args.json:
        print(json.dumps(manifest, indent=1, ensure_ascii=False))
    else:
        print(f"materials package : {manifest['package_id']}")
        print(f"inputs            : {report['inputs']['core_id']} / {report['inputs']['match_batch_id']}")
        print(
            "materials         : "
            f"{manifest['counts']['materials']} 个组件"
            f"（{report['materials']['by_material']}）"
        )
        print(
            "availability      : "
            f"A {manifest['counts']['tier_a_fields']} 个字段 / "
            f"B {manifest['counts']['tier_b_fields']} 个字段；"
            f"仅受支持子集可报的指标 {manifest['counts']['subset_only_metrics']} 个"
        )
        for conclusion in report["conclusions"]:
            print(f"{conclusion['id']}: {conclusion['text']}")
        print(
            "wording           : "
            f"{manifest['counts']['wording_checks']} 条核对，"
            f"未通过 {manifest['counts']['wording_failures']} 条"
        )
        print(f"data_digest       : {manifest['digests']['data_digest']}")
        print(f"outputs           : {manifest['outputs']}")

    digest = manifest["digests"]["data_digest"]
    if args.expect_data_digest and args.expect_data_digest.lower() != digest.lower():
        print(
            f"数据摘要不一致：期望 {args.expect_data_digest} 实得 {digest}",
            file=sys.stderr,
        )
        return EXIT_DIGEST_MISMATCH
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
