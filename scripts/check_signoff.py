"""Read-only helper for the 26-row human sign-off sheet.

For a row in `data/derived/manual_signoff_sample.csv`, it re-reads the fixed log
snapshot, queries the archive endpoint at the declared block, and prints the
table value next to the on-chain value. It never edits the sheet.

Usage:
    python scripts/check_signoff.py --row 2
    python scripts/check_signoff.py --all
    python scripts/check_signoff.py --validate
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from recon.config import load_config
from recon.contracts import decode_event_log, encode_address_call, word_at, word_to_int, verify_topic_table
from recon.pipeline import blacklist_state_label
from recon.rpc import RpcCache

ROOT = Path(__file__).resolve().parents[1]
SHEET = ROOT / "data/derived/manual_signoff_sample.csv"
CORE = ROOT / "data/recon/core_64c8c7f9452291663926113a2b1ecc20"


def load_snapshot_index() -> dict[tuple[str, int], dict]:
    snapshot = json.loads((ROOT / "data/raw/logs/ethereum_blacklist_logs.json").read_text(encoding="utf-8"))
    index = {}
    for log in snapshot["logs"]:
        key = (log["transactionHash"].lower(), int(log["logIndex"], 16))
        index[key] = log
    return index


def main() -> None:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--row", type=int, help="Excel 行号（表头为第 1 行）")
    group.add_argument("--all", action="store_true")
    group.add_argument("--validate", action="store_true")
    args = parser.parse_args()

    cfg = load_config()
    rpc = RpcCache(cfg)
    topics = verify_topic_table(cfg.registry)
    sheet = pd.read_csv(SHEET, dtype=str).fillna("")
    events = pd.read_csv(CORE / "events.csv", dtype=str)
    event_map = events.set_index("event_id")
    snapshot = load_snapshot_index()

    def query(token: str, address: str, block: int, kind: str) -> str:
        contract = cfg.tokens[token]["contract"]
        if kind == "state":
            signature = cfg.tokens[token]["blacklist_state_function"]
            call = rpc.state_call(
                to=contract,
                data=encode_address_call(signature, address),
                block_number=block,
                label=f"signoff_state_{block}",
            )
            return blacklist_state_label(call.result)
        call = rpc.state_call(
            to=contract,
            data=encode_address_call("balanceOf(address)", address),
            block_number=block,
            label=f"signoff_balance_{block}",
        )
        raw = word_to_int(word_at(call.result, 0))
        return str(Decimal(raw) / (Decimal(10) ** int(cfg.tokens[token]["decimals"])))

    def check(row: pd.Series) -> dict:
        token = row["token_symbol"]
        address = row["affected_address"]
        block = int(row["block_number"])
        if row["row_type"] == "d2":
            state = query(token, address, block, "state")
            balance = query(token, address, block, "balance")
            ok = state == row["state_before"] and abs(Decimal(balance) - Decimal(row["balance_token"])) < Decimal("0.000001")
            return {"ok": ok, "state": state, "balance": balance, "log_note": "D2：无交易，核对地址页", "adjustment": "0"}

        tx = row["tx_hash"].lower()
        log_index = int(float(row["log_index"]))
        log = snapshot.get((tx, log_index))
        if log is None:
            return {"ok": False, "state": "?", "balance": "?", "log_note": "固定快照中找不到该 (tx, log_index)", "adjustment": "?"}
        topic = log["topics"][0].lower()
        spec = topics.get(topic)
        decoded, _, _ = decode_event_log(log, spec)
        log_note = f"快照命中 {spec.signature}，解码地址 {decoded}"
        before = query(token, address, block - 1, "state")
        after = query(token, address, block, "state")
        balance = query(token, address, block - 1, "balance")
        adjustment = event_map.loc[row["event_id"], "balance_pre_event_adjustment_raw"] if row["event_id"] in event_map.index else "0"
        ok = (
            before == row["state_before"]
            and after == row["state_after"]
            and decoded == address
            and abs(Decimal(balance) + Decimal(adjustment) - Decimal(row["balance_token"])) < Decimal("0.000001")
        )
        return {"ok": ok, "state": f"{before} -> {after}", "balance": balance, "log_note": log_note, "adjustment": adjustment}

    if args.validate:
        missing = sheet[
            (sheet["reviewer"].str.strip() == "")
            | (sheet["reviewed_at_utc"].str.strip() == "")
            | (~sheet["decision"].isin(["match", "mismatch", "unverifiable"]))
        ]
        results = [(int(row["sheet_row"]), check(row)) for _, row in sheet.iterrows()]
        bad = [(n, r) for n, r in results if not r["ok"]]
        print(f"rows           : {len(sheet)}")
        print(f"unsigned       : {len(missing)}")
        print(f"numeric check  : {len(results) - len(bad)} ok / {len(bad)} differ")
        for row_no, result in bad:
            print(f"  row {row_no}: {result['log_note']} | {result['state']} | {result['balance']}")
        if len(missing) or bad:
            raise SystemExit(4)
        print("sign-off sheet : complete and numerically consistent")
        return

    rows = sheet if args.all else sheet[sheet["sheet_row"].astype(int) == args.row]
    if rows.empty:
        raise SystemExit(f"找不到 Excel 行 {args.row}")
    for _, row in rows.iterrows():
        result = check(row)
        print(
            f"行 {row['sheet_row']} | {row['row_type']} | {row['token_symbol']} {row['event_class']} | "
            f"block {row['block_number']}"
        )
        print(f"  表格: before={row['state_before']} after={row['state_after']} balance={row['balance_token']} ({row['balance_reference']})")
        print(f"  日志: {result['log_note']}")
        print(f"  链上: state={result['state']} balance(block-1/cutoff)={result['balance']} adjustment={result['adjustment']}")
        print(f"  数值: {'一致' if result['ok'] else '不一致，请查 note 字段'}")
        print("  下一步: 打开 etherscan_url 目视确认日志位置与地址，填写 reviewer / reviewed_at_utc / decision / note")
        if row["etherscan_url"]:
            print(f"  URL : {row['etherscan_url']}")
        else:
            print(f"  URL : {row['address_url']}")


if __name__ == "__main__":
    main()
