"""Reconstruct one freeze lifecycle case: several transactions, one ordered sequence.

Each case is declared in ``config/lifecycle_cases.json`` and runs the same pipeline
as the single-event reconstruction; the case layer only merges the outputs and adds
checks against independent references (an independent provider's receipts, the
ticket-01 manually checked sample, the USDC proxy upgrade boundary).

    python scripts/reconstruct_lifecycle.py --case unfreeze
    python scripts/reconstruct_lifecycle.py --case all
    python scripts/reconstruct_lifecycle.py --case destruction --expect-data-digest <hex>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from recon.config import load_config  # noqa: E402
from recon.lifecycle import CASES_PATH, CaseRun, load_cases  # noqa: E402
from recon.rpc import RpcError  # noqa: E402

EXIT_RUN_ERROR = 1
EXIT_DIGEST_MISMATCH = 2
EXIT_VALIDATION_MISMATCH = 3


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", default="all", help="案例 id 或 all")
    parser.add_argument(
        "--cutoff-block",
        type=int,
        default=None,
        help="study_cutoff_block；缺省取日志快照的 end_block（不随运行时刻漂移）",
    )
    parser.add_argument("--cases", default=CASES_PATH)
    parser.add_argument("--log-snapshot", default="data/raw/logs/ethereum_blacklist_logs.json")
    parser.add_argument("--contract-registry", default="config/contracts.json")
    parser.add_argument("--dictionary", default="docs/data-dictionary.csv")
    parser.add_argument("--out-root", default="data/recon")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="忽略缓存重新抓取（会改写 *_at_utc 时间戳，但数据摘要应保持不变）",
    )
    parser.add_argument(
        "--expect-data-digest",
        default=None,
        help="与产物数据摘要比对；不一致时退出码 2（仅限单案例运行）",
    )
    parser.add_argument("--json", action="store_true", help="打印每个案例的完整 manifest")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cfg = load_config(
        log_snapshot=args.log_snapshot,
        contract_registry=args.contract_registry,
        dictionary_csv=args.dictionary,
        out_root=args.out_root,
        refresh=args.refresh,
    )
    cases, cases_path = load_cases(cfg, args.cases)

    if args.cutoff_block is None:
        snapshot = json.loads(cfg.log_snapshot.read_text(encoding="utf-8"))
        cutoff_block = int(snapshot["end_block"])
    else:
        cutoff_block = args.cutoff_block

    if args.case == "all":
        selected = list(cases["cases"])
    elif args.case in cases["cases"]:
        selected = [args.case]
    else:
        print(
            f"未知案例 {args.case}；可选: {', '.join(cases['cases'])}, all",
            file=sys.stderr,
        )
        return 1
    if args.expect_data_digest and len(selected) != 1:
        print("--expect-data-digest 只能与单案例一起使用", file=sys.stderr)
        return 1

    failures = 0
    errors = 0
    digest_mismatch = False
    for case_id in selected:
        run = CaseRun(
            cfg=cfg,
            case_id=case_id,
            case=cases["cases"][case_id],
            cutoff_block=cutoff_block,
            cases_path=cases_path,
        )
        try:
            outcome = run.run()
            manifest = run.deliver(outcome)
        except (RpcError, SystemExit, ValueError, KeyError) as exc:
            # 单个案例失败不影响其余案例（--case all 时逐案隔离，退出码 1）。
            print(f"{case_id}: 运行失败：{exc}", file=sys.stderr)
            errors += 1
            continue

        agreement = manifest["counts"]["validation_agreement"]
        bad = agreement.get("mismatch", 0) + len(manifest["expectations_failed"])
        digest = manifest["digests"]["data_digest"]

        if args.json:
            print(json.dumps(manifest, indent=1, ensure_ascii=False))
        else:
            print(f"case_id           : {manifest['case_id']}")
            print(f"title             : {manifest['title']}")
            print(
                f"events            : {manifest['counts']['events']} "
                f"{manifest['counts']['event_class']}"
            )
            print(
                f"balances          : {manifest['counts']['balances']} "
                f"{manifest['counts']['balance_reference']}"
            )
            print(
                "validation        : "
                + ", ".join(f"{key}={value}" for key, value in agreement.items())
            )
            if manifest["expectations_failed"]:
                for item in manifest["expectations_failed"]:
                    print(
                        f"  expectation failed: {item['field']} "
                        f"declared={item['declared']} observed={item['observed']}"
                    )
            for boundary in manifest["version_boundaries"]:
                print(
                    f"version boundary  : block {boundary['block']} "
                    f"{boundary['implementation_before']} -> "
                    f"{boundary['implementation_after']} "
                    f"({boundary['version_label_before'] or 'unregistered'} -> "
                    f"{boundary['version_label_after'] or 'unregistered'})"
                )
            print(f"data_digest       : {digest}")
            print(f"artifact_digest   : {manifest['digests']['artifact_digest']}")
            print(f"outputs           : {manifest['outputs']}")

        if bad:
            failures += 1
        if (
            args.expect_data_digest
            and args.expect_data_digest.lower() != digest.lower()
        ):
            digest_mismatch = True
            print(
                f"{case_id}: 数据摘要不一致：期望 {args.expect_data_digest} 实得 {digest}",
                file=sys.stderr,
            )

    if digest_mismatch:
        return EXIT_DIGEST_MISMATCH
    if errors:
        return EXIT_RUN_ERROR
    return EXIT_VALIDATION_MISMATCH if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
