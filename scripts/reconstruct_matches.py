"""Attach the match layers to the core event set and report their coverage (ticket 06).

Reads the delivered core bundle (ticket 05) and writes, into a separate directory:

* ``matches.csv``: one row per event per layer (``ofac_sdn``, ``exchange_label``),
  with match evidence and label dates, no filtering of events;
* ``missingness.csv``: field-level missing counts with reason codes for ``events``,
  ``balances`` and ``matches``;
* ``report.json``: public-record match rates, missing values, label dates, the size
  of the dated subsets, and the statements that keep them descriptive;
* ``manifest.json``: inputs, digests and counts.

    python scripts/reconstruct_matches.py
    python scripts/reconstruct_matches.py --check
    python scripts/reconstruct_matches.py --expect-data-digest <hex>

No network access is needed: every input is a snapshot on disk.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from recon.config import load_config  # noqa: E402
from recon.matching import LABEL_PATH, MatchingRun  # noqa: E402

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
    parser.add_argument("--labels", default=LABEL_PATH)
    parser.add_argument("--dictionary", default="docs/data-dictionary.csv")
    parser.add_argument("--out-root", default="data/recon")
    parser.add_argument(
        "--expect-data-digest",
        default=None,
        help="与 matches 数据摘要比对；不一致时退出码 2",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="只复核已交付产物：重型重建匹配表、比对报告与缺失统计、跑规则自检，不访问网络",
    )
    parser.add_argument("--json", action="store_true", help="打印完整 manifest")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cfg = load_config(
        dictionary_csv=args.dictionary,
        out_root=args.out_root,
    )

    run = MatchingRun(cfg=cfg, label_path=args.labels, core_id=args.core_id)

    if args.check:
        problems = run.check()
        if problems:
            for problem in problems:
                print(f"match check: {problem}", file=sys.stderr)
            return EXIT_CHECK_FAILED
        print(
            "match check       : ok（重建一致、证据齐全、缺失带原因码、规则自检通过）"
        )
        return 0

    try:
        outcome = run.run()
        manifest = run.deliver(outcome)
    except (SystemExit, ValueError, KeyError) as exc:
        print(f"运行失败：{exc}", file=sys.stderr)
        return EXIT_RUN_ERROR

    report = outcome["report"]
    rates = report["public_record_match_rates"]
    if args.json:
        print(json.dumps(manifest, indent=1, ensure_ascii=False))
    else:
        print(f"match batch       : {manifest['match_batch_id']}")
        print(
            "core set          : "
            f"{report['scope']['core_id']} "
            f"（{report['scope']['core_events']} 事件 / "
            f"{report['scope']['unique_addresses']} 地址）"
        )
        print(f"matches rows      : {manifest['counts']['matches']} {manifest['counts']['by_status']}")
        ofac = rates["ofac_sdn"]["by_event"]
        print(
            "match rate (ofac) : "
            f"{ofac['rate']} ({ofac['numerator']}/{ofac['denominator']} 事件)"
            f"；地址 {rates['ofac_sdn']['addresses']['matched']}"
            f"/{rates['ofac_sdn']['addresses']['total']}"
        )
        labels = rates["exchange_label"]["by_event"]
        print(
            "match rate (label): "
            f"{labels['rate']} ({labels['rate_status']}："
            f"{rates['exchange_label']['not_reportable_reason']}）"
        )
        subsets = report["analysis_subsets"]
        print(
            "dated subsets     : "
            f"sanctions {subsets['dated_sanctions']['events']}、"
            f"exchange {subsets['dated_exchange_label']['events']}"
        )
        print(
            "missingness       : "
            f"{report['missing_values']['fields_with_missing']}"
            f"/{report['missing_values']['fields']} 个字段有缺失"
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
