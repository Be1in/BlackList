"""Reconstruct the full core event set inside the study range (ticket 05).

Inputs are the fixed log snapshot and the archival state endpoint; the observation
window comes from ``config/study_scope.json`` (ticket 04's decision), not from the
wall clock. Output is one bundle under ``data/recon/core_<id>/``:

* ``events.csv``: every retrievable event in the window, no matching filter;
* ``balances.csv``: D1 / at_event / D2 rows;
* ``validation.csv``: position, version, balance, public-record and table-level checks;
* ``chain_registry.csv``: chains in scope and chains registered but not collected;
* ``coverage_gaps.csv`` / ``scope_excluded.csv``: the coverage record and every
  event excluded by the observation window, one row each;
* ``manifest.json``: coverage, balance error, unverifiable share, exclusion counts.

    python scripts/reconstruct_core.py
    python scripts/reconstruct_core.py --workers 12
    python scripts/reconstruct_core.py --expect-data-digest <hex>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from recon.config import load_config  # noqa: E402
from recon.core import DECISION_PATH, CoreRun  # noqa: E402
from recon.rpc import RpcError  # noqa: E402

EXIT_RUN_ERROR = 1
EXIT_DIGEST_MISMATCH = 2
EXIT_VALIDATION_MISMATCH = 3
EXIT_CHECK_FAILED = 4


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--start-block", type=int, default=None, help="缺省取研究范围决策的观察期起点"
    )
    parser.add_argument(
        "--cutoff-block", type=int, default=None, help="缺省取研究范围决策的研究截止区块"
    )
    parser.add_argument("--decision", default=DECISION_PATH)
    parser.add_argument(
        "--workers", type=int, default=8, help="并发重建/核对线程数（只影响速度，不影响结果）"
    )
    parser.add_argument(
        "--max-events",
        type=int,
        default=None,
        help="冒烟运行：只重建前 N 个事件；产物标记 partial_run，不作为交付口径",
    )
    parser.add_argument("--log-snapshot", default="data/raw/logs/ethereum_blacklist_logs.json")
    parser.add_argument("--contract-registry", default="config/contracts.json")
    parser.add_argument("--dictionary", default="docs/data-dictionary.csv")
    parser.add_argument("--out-root", default="data/recon")
    parser.add_argument("--refresh", action="store_true", help="忽略缓存重新抓取")
    parser.add_argument(
        "--expect-data-digest",
        default=None,
        help="与产物数据摘要比对；不一致时退出码 2",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="只复核已交付产物：重算摘要、列集合与不变量，不访问网络",
    )
    parser.add_argument("--json", action="store_true", help="打印完整 manifest")
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

    decision = json.loads(cfg.path(args.decision).read_text(encoding="utf-8"))
    start_block = (
        args.start_block
        if args.start_block is not None
        else int(decision["observation_start_block"])
    )
    cutoff_block = (
        args.cutoff_block
        if args.cutoff_block is not None
        else int(decision["study_cutoff_block"])
    )

    run = CoreRun(
        cfg=cfg,
        start_block=start_block,
        cutoff_block=cutoff_block,
        decision_path=args.decision,
        workers=args.workers,
        max_events=args.max_events,
    )

    if args.check:
        problems = run.check()
        if problems:
            for problem in problems:
                print(f"core check: {problem}", file=sys.stderr)
            return EXIT_CHECK_FAILED
        print("core check        : ok（摘要、列集合、链登记、排除与覆盖算术一致）")
        return 0

    try:
        outcome = run.run()
        manifest = run.deliver(outcome)
    except (RpcError, SystemExit, ValueError, KeyError) as exc:
        print(f"运行失败：{exc}", file=sys.stderr)
        return EXIT_RUN_ERROR

    if args.json:
        print(json.dumps(manifest, indent=1, ensure_ascii=False))
    else:
        coverage = manifest["metrics"]["event_retrieval_coverage"]
        error = manifest["metrics"]["balance_reconstruction_error"]
        unverifiable = manifest["metrics"]["unverifiable_share"]
        core_set = manifest["metrics"]["core_set"]
        print(f"core_id           : {manifest['core_id']}")
        print(
            "events            : "
            f"{manifest['counts']['events']} {manifest['counts']['event_class']}"
        )
        print(
            "chains            : "
            + ", ".join(core_set["chains"])
            + f"（chain_registry {manifest['counts']['chain_registry']} 行）"
        )
        print(
            "retrieval coverage: "
            f"{coverage['coverage']} ({coverage['matched_events']}/"
            f"{coverage['expected_events']} independent logs; "
            f"snapshot_only={coverage['snapshot_only_events']}, "
            f"reference_only={coverage['reference_only_events']})"
        )
        print(
            "block parity      : "
            f"{coverage['block_level_log_parity']['matched']}"
            f"/{coverage['block_level_log_parity']['blocks']}"
        )
        print(
            "balance error     : "
            f"max {error['max_abs_error_raw']} raw / "
            f"{error['max_abs_error_token']} token, "
            f"{error['exact_matches']}/{error['checks']} exact"
        )
        print(
            "unverifiable share: "
            f"{unverifiable['share']} ({unverifiable['unverifiable']}"
            f"/{unverifiable['validation_rows']})"
        )
        print(
            "excluded          : "
            f"{manifest['counts']['excluded']} "
            f"({manifest['metrics']['exclusions']['by_rule_id']}); "
            f"outside window {manifest['counts']['outside_observation_window']}; "
            "match-based 0"
        )
        print(f"data_digest       : {manifest['digests']['data_digest']}")
        print(f"artifact_digest   : {manifest['digests']['artifact_digest']}")
        print(f"outputs           : {manifest['outputs']}")

    digest = manifest["digests"]["data_digest"]
    if args.expect_data_digest and args.expect_data_digest.lower() != digest.lower():
        print(
            f"数据摘要不一致：期望 {args.expect_data_digest} 实得 {digest}",
            file=sys.stderr,
        )
        return EXIT_DIGEST_MISMATCH
    bad = manifest["counts"]["validation_agreement"].get("mismatch", 0)
    return EXIT_VALIDATION_MISMATCH if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
