"""Validate the representative sample, then check the study-scope decision.

Run the sample (validation first)::

    python scripts/validate_sample.py
    python scripts/validate_sample.py --expect-data-digest <hex>

Then, after ``config/study_scope.json`` cites that artifact, check the decision::

    python scripts/validate_sample.py --check-scope

``--check-scope`` never touches the network: it re-derives the data digest from the
delivered CSVs and compares the decision's cited digest, metrics and observation
bounds with the manifest.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from recon.config import load_config  # noqa: E402
from recon.rpc import RpcError  # noqa: E402
from recon.sample import SAMPLE_RECORDS, SCOPE_PATH, SampleValidation  # noqa: E402

EXIT_RUN_ERROR = 1
EXIT_DIGEST_MISMATCH = 2
EXIT_VALIDATION_MISMATCH = 3
EXIT_SCOPE_CHECK_FAILED = 4


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cutoff-block",
        type=int,
        default=None,
        help="study_cutoff_block；缺省取日志快照的 end_block（不随运行时刻漂移）",
    )
    parser.add_argument("--sample", default=SAMPLE_RECORDS)
    parser.add_argument("--cases", default="config/lifecycle_cases.json")
    parser.add_argument("--scope", default=SCOPE_PATH)
    parser.add_argument("--log-snapshot", default="data/raw/logs/ethereum_blacklist_logs.json")
    parser.add_argument("--contract-registry", default="config/contracts.json")
    parser.add_argument("--dictionary", default="docs/data-dictionary.csv")
    parser.add_argument("--out-root", default="data/recon")
    parser.add_argument("--refresh", action="store_true", help="忽略缓存重新抓取")
    parser.add_argument(
        "--expect-data-digest",
        default=None,
        help="与样本数据摘要比对；不一致时退出码 2",
    )
    parser.add_argument(
        "--check-scope",
        action="store_true",
        help="只校验范围决策：重算交付摘要并比对决策引用，不访问网络",
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

    if args.cutoff_block is None:
        snapshot = json.loads(cfg.log_snapshot.read_text(encoding="utf-8"))
        cutoff_block = int(snapshot["end_block"])
    else:
        cutoff_block = args.cutoff_block

    validation = SampleValidation(
        cfg=cfg,
        cutoff_block=cutoff_block,
        sample_path=args.sample,
        cases_path=args.cases,
        scope_path=args.scope,
    )

    if args.check_scope:
        problems = validation.check_scope(sample_path=args.sample)
        if problems:
            for problem in problems:
                print(f"scope check: {problem}", file=sys.stderr)
            return EXIT_SCOPE_CHECK_FAILED
        print("scope check      : ok（验证在前、范围决策在后，且决策引用的指标与摘要一致）")
        return 0

    try:
        outcome = validation.run()
        manifest = validation.deliver(outcome)
    except (RpcError, SystemExit, ValueError, KeyError) as exc:
        print(f"运行失败：{exc}", file=sys.stderr)
        return EXIT_RUN_ERROR

    if args.json:
        print(json.dumps(manifest, indent=1, ensure_ascii=False))
    else:
        metrics = manifest["metrics"]
        coverage = metrics["event_retrieval_coverage"]
        error = metrics["balance_reconstruction_error"]
        unverifiable = metrics["unverifiable_share"]
        print(f"validation_id     : {manifest['validation_id']}")
        print(
            "events            : "
            f"{manifest['counts']['events']} "
            f"{manifest['counts']['event_class']}"
        )
        print(
            "sample matrix     : "
            + "; ".join(
                f"{token}={'/'.join(classes)}"
                for token, classes in metrics["sample_matrix"][
                    "event_classes_by_token"
                ].items()
            )
        )
        print(
            "retrieval coverage: "
            f"{coverage['coverage']} "
            f"({coverage['matched_events']}/{coverage['expected_events']} logs; "
            f"{coverage['slots_covered']}/{coverage['declared_sample_slots']} slots "
            "both reconstructed and independently retrieved)"
        )
        print(
            "block parity      : "
            f"{coverage['block_level_log_parity']['matched']}"
            f"/{coverage['block_level_log_parity']['blocks']}"
        )
        print(
            "balance error     : "
            f"max {error['max_abs_error_raw']} raw / {error['max_abs_error_token']} token, "
            f"{error['exact_matches']}/{error['checks']} exact"
        )
        print(
            "unverifiable share: "
            f"{unverifiable['share']} ({unverifiable['unverifiable']}"
            f"/{unverifiable['validation_rows']})"
        )
        print(f"data_digest       : {manifest['digests']['data_digest']}")
        print(f"artifact_digest   : {manifest['digests']['artifact_digest']}")
        print(f"outputs           : {manifest['outputs']}")

    bad = manifest["counts"]["validation_agreement"].get("mismatch", 0)
    digest = manifest["digests"]["data_digest"]
    if args.expect_data_digest and args.expect_data_digest.lower() != digest.lower():
        print(
            f"数据摘要不一致：期望 {args.expect_data_digest} 实得 {digest}",
            file=sys.stderr,
        )
        return EXIT_DIGEST_MISMATCH
    return EXIT_VALIDATION_MISMATCH if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
