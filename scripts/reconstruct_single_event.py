"""Reconstruct one Ethereum USDT/USDC freeze event from logs + archival state.

Default target is the known USDT freeze of sample slot S1
(block 26,292,598, tx 0x5e520c7b…72bd4, log 60). Output: one event record, the
two balance rows (D1 at_freeze / D2 at_study_cutoff) and the validation rows,
plus a manifest carrying input digests and the reproducibility digests.

    python scripts/reconstruct_single_event.py
    python scripts/reconstruct_single_event.py --refresh --expect-data-digest <hex>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from recon.config import load_config  # noqa: E402
from recon.pipeline import Reconstruction  # noqa: E402

DEFAULT_TX_HASH = "0x5e520c7bff1f053a6a319397b1518f15923c23329e0639b6fc66a48f47772bd4"
DEFAULT_LOG_INDEX = 60


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tx-hash", default=DEFAULT_TX_HASH)
    parser.add_argument("--log-index", type=int, default=DEFAULT_LOG_INDEX)
    parser.add_argument(
        "--token",
        default="USDT",
        help="期望的事件代币；与快照命中的合约不一致时报错退出",
    )
    parser.add_argument(
        "--cutoff-block",
        type=int,
        default=None,
        help="study_cutoff_block；缺省取日志快照的 end_block（本记录内固定，不随运行时刻变化）",
    )
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
        help="与产物数据摘要比对；不一致时退出码 2（用于重复运行的等价性检查）",
    )
    parser.add_argument("--json", action="store_true", help="打印完整 manifest JSON")
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

    reconstruction = Reconstruction(
        cfg=cfg,
        tx_hash=args.tx_hash,
        log_index=args.log_index,
        cutoff_block=cutoff_block,
        expected_token=args.token,
    )
    result = reconstruction.run()
    manifest = reconstruction.deliver(result)

    digest = manifest["digests"]["data_digest"]
    event = result["events"][0]
    balances = result["balances"]
    validation = result["validation"]

    if args.json:
        print(json.dumps(manifest, indent=1, ensure_ascii=False))
    else:
        print(f"reconstruction_id : {manifest['reconstruction_id']}")
        print(f"event_id          : {event['event_id']}")
        print(
            f"event             : {event['token_symbol']} {event['event_class']} "
            f"block {event['block_number']} log {event['log_index']} "
            f"{event['block_time_utc']}"
        )
        print(f"affected_address  : {event['affected_address']}")
        print(
            f"contract          : {event['contract_version_label']} "
            f"({event['contract_code_hash'][:18]}…) "
            f"proxy={event['is_proxy']}"
        )
        print(
            "blacklist state   : "
            f"{event['blacklist_state_before']} → {event['blacklist_state_after']}"
        )
        print(
            f"balance_pre_event : {event['balance_pre_event_token']} "
            f"({event['balance_pre_event_raw']} raw, adj "
            f"{event['balance_pre_event_adjustment_raw']})"
        )
        for row in balances:
            print(
                f"balance [{row['balance_reference']}] : {row['balance_token']} "
                f"at block {row['balance_block_number']}"
            )
        print(f"validation        : {len(validation)} rows, "
              + ", ".join(
                  f"{value}={sum(1 for r in validation if r['agreement'] == value)}"
                  for value in sorted({r["agreement"] for r in validation})
              ))
        print(f"data_digest       : {digest}")
        print(f"artifact_digest   : {manifest['digests']['artifact_digest']}")
        print(f"outputs           : {manifest['outputs']}")

    if args.expect_data_digest and args.expect_data_digest.lower() != digest.lower():
        print(
            f"数据摘要不一致：期望 {args.expect_data_digest} 实得 {digest}",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
