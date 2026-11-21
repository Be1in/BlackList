"""Restricted-subset analyses: timing gaps, outflows, frozen balances (ticket 08).

Reads the delivered core bundle (ticket 05) and match bundle (ticket 06) and writes its
own bundle, kept strictly apart from the core comparison:

* ``subset_eligibility.csv``: the filter chain that defines each restricted subset,
  step by step (rule text, events in / dropped / remaining, drop reason codes);
* ``timing_gaps.csv``: public sanctions timing gaps on the dated-sanctions subset;
* ``exchange_outflows.csv``: pre-freeze address outflows on the dated-label subset,
  inside the baseline window;
* ``frozen_balances.csv``: D1 frozen balances on each subset (D1 only, declared);
* ``outflow_window_sensitivity.csv``: the same outflow metrics under every window;
* ``report.json`` / ``manifest.json``: rules, subset sizes, caveats, digests.

    python scripts/reconstruct_restricted.py
    python scripts/reconstruct_restricted.py --check
    python scripts/reconstruct_restricted.py --expect-data-digest <hex>
    python scripts/reconstruct_restricted.py --outflow-window 72h

No network access is needed: every input is a snapshot on disk.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from recon.config import load_config  # noqa: E402
from recon.restricted import (  # noqa: E402
    BASELINE,
    TRANSFERS_PATH,
    WINDOW_SETTINGS,
    RestrictedRun,
)

EXIT_RUN_ERROR = 1
EXIT_DIGEST_MISMATCH = 2
EXIT_CHECK_FAILED = 4


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--core-id",
        default="",
        help="核心集批次目录名；缺省按输入摘要派生（不猜最新目录）",
    )
    parser.add_argument(
        "--outflow-window",
        choices=list(WINDOW_SETTINGS),
        default=BASELINE["outflow_window"],
        help="冻结前窗口长度；其余窗口仍会写进敏感性表",
    )
    parser.add_argument(
        "--transfers",
        default=TRANSFERS_PATH,
        help="转账日志快照；缺省指向 data/raw/transfers/（不存在则按不可报告处理）",
    )
    parser.add_argument("--dictionary", default="docs/data-dictionary.csv")
    parser.add_argument("--out-root", default="data/recon")
    parser.add_argument(
        "--expect-data-digest",
        default=None,
        help="与受限分析表的数据摘要比对；不一致时退出码 2",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="只复核已交付产物：重建五张表、比对报告、核对筛选链与分离性，不访问网络",
    )
    parser.add_argument("--json", action="store_true", help="打印完整 manifest")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cfg = load_config(
        dictionary_csv=args.dictionary,
        out_root=args.out_root,
    )
    run = RestrictedRun(
        cfg=cfg,
        core_id=args.core_id,
        outflow_window=args.outflow_window,
        transfers_path=args.transfers,
    )

    if args.check:
        problems = run.check()
        if problems:
            for problem in problems:
                print(f"restrict check: {problem}", file=sys.stderr)
            return EXIT_CHECK_FAILED
        print(
            "restrict check    : ok（五张表可重建、筛选链算术一致、分离性成立、规则自检通过）"
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
        scope = report["scope"]
        print(f"restrict batch    : {manifest['restrict_batch_id']}")
        print(
            "scope             : "
            f"{scope['core_id']} / {scope['match_batch_id']} "
            f"（区块 {scope['observation_window']['start_block']:,}–"
            f"{scope['observation_window']['cutoff_block']:,}）"
        )
        print(
            "subsets           : "
            f"timing-gap {report['subsets']['timing_gap']}；"
            f"exchange-outflow {report['subsets']['exchange_outflow']}"
        )
        print(f"transfer snapshot : {scope['transfers']['available']}（{scope['transfers']['path']}）")
        for conclusion in report["conclusions"]:
            print(f"{conclusion['id']}: {conclusion['text']}")
        print(f"parameters        : {report['parameters']['active']}")
        print(
            "sensitivity       : "
            f"{manifest['counts']['sensitivity_rows']} 行，"
            f"not_assessable {report['tables']['sensitivity_not_assessable']} 行"
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
