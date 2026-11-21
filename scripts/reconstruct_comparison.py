"""Compare the two issuers under one scope, with sensitivity (ticket 07).

Reads the delivered core bundle (ticket 05) and match bundle (ticket 06), refuses to
compare when the observation window, event definitions or missing-value treatment
differ, and writes a separate bundle:

* ``comparison.csv``: one row per metric per issuer, each with its coverage scope,
  unit, denominator and ``descriptive`` statement type;
* ``differences.csv``: the USDT − USDC difference per metric, or an explicit
  ``not_comparable`` reason (structural zero / not reportable);
* ``sensitivity.csv``: timestamp precision (second vs day) and missing-label
  treatment (drop undated vs retain unlabelled), each row compared with the baseline;
* ``report.json``: preconditions, parameter grid, descriptive conclusions, required
  caveats and the guard self-test;
* ``manifest.json``: inputs, digests and counts.

    python scripts/reconstruct_comparison.py
    python scripts/reconstruct_comparison.py --check
    python scripts/reconstruct_comparison.py --expect-data-digest <hex>
    python scripts/reconstruct_comparison.py --timestamp-precision day

No network access is needed: every input is a snapshot on disk.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from recon.comparison import (  # noqa: E402
    BASELINE,
    MISSING_LABEL_SETTINGS,
    PRECISION_SETTINGS,
    ComparisonPreconditionError,
    ComparisonRun,
)
from recon.config import load_config  # noqa: E402

EXIT_RUN_ERROR = 1
EXIT_DIGEST_MISMATCH = 2
EXIT_PRECONDITION_FAILED = 3
EXIT_CHECK_FAILED = 4


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--core-id",
        default="",
        help="核心集批次目录名；缺省按输入摘要派生（不猜最新目录）",
    )
    parser.add_argument(
        "--timestamp-precision",
        choices=list(PRECISION_SETTINGS),
        default=BASELINE["timestamp_precision"],
        help="时长口径：second 用区块秒级时间，day 截断到整日",
    )
    parser.add_argument(
        "--missing-labels",
        choices=list(MISSING_LABEL_SETTINGS),
        default=BASELINE["missing_labels"],
        help=(
            "缺失标签处理：drop_undated 只用已尝试的标签匹配行作分母；"
            "retain_unlabelled 把未标注事件计入分母（分子不变）"
        ),
    )
    parser.add_argument("--dictionary", default="docs/data-dictionary.csv")
    parser.add_argument("--out-root", default="data/recon")
    parser.add_argument(
        "--expect-data-digest",
        default=None,
        help="与比较表数据摘要比对；不一致时退出码 2",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "只复核已交付产物：重算前置条件、比较表、报告与敏感性对照，不访问网络、不写盘"
        ),
    )
    parser.add_argument("--json", action="store_true", help="打印完整 manifest")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cfg = load_config(
        dictionary_csv=args.dictionary,
        out_root=args.out_root,
    )
    run = ComparisonRun(
        cfg=cfg,
        core_id=args.core_id,
        timestamp_precision=args.timestamp_precision,
        missing_labels=args.missing_labels,
    )

    if args.check:
        problems = run.check()
        if problems:
            for problem in problems:
                print(f"compare check: {problem}", file=sys.stderr)
            return EXIT_CHECK_FAILED
        print(
            "compare check    : ok（前置条件成立、比较表可重建、报告与敏感性一致、"
            "规则自检通过）"
        )
        return 0

    try:
        outcome = run.run()
        manifest = run.deliver(outcome)
    except ComparisonPreconditionError as exc:
        print(f"运行失败（未写产物）：{exc}", file=sys.stderr)
        return EXIT_PRECONDITION_FAILED
    except (SystemExit, ValueError, KeyError, OSError) as exc:
        print(f"运行失败：{exc}", file=sys.stderr)
        return EXIT_RUN_ERROR

    report = outcome["report"]
    if args.json:
        print(json.dumps(manifest, indent=1, ensure_ascii=False))
    else:
        scope = report["scope"]
        print(f"compare batch     : {manifest['compare_batch_id']}")
        print(
            "scope             : "
            f"{scope['core_id']} / {scope['match_batch_id']} "
            f"（区块 {scope['observation_window']['start_block']:,}–"
            f"{scope['observation_window']['cutoff_block']:,}）"
        )
        print(f"preconditions     : {manifest['preconditions']}")
        for conclusion in report["conclusions"]:
            print(f"{conclusion['id']} ({conclusion['coverage_scope']}): {conclusion['text']}")
        print(f"parameters        : {report['parameters']['active']}")
        print(
            "sensitivity       : "
            f"{manifest['counts']['sensitivity_rows']} 行，"
            f"结论受影响 {len(report['tables']['sensitivity_conclusion_affected'])} 项"
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
