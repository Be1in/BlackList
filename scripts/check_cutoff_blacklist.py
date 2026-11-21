"""Quantify the D2 population gap: addresses blacklisted at the study cutoff
that have no event inside the observation window.

Reconstructs the full blacklist state from the fixed raw log snapshot (all
6,060 logs, including the 90 pre-window USDT events), compares it with the
delivered `at_study_cutoff` rows, and queries `isBlackListed` / `balanceOf`
for the missing addresses through the project's replay cache.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from recon.config import load_config
from recon.contracts import encode_address_call, word_to_int
from recon.rpc import RpcCache

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / ".backup/20261117_v1_replaced/core_64c8c7f9452291663926113a2b1ecc20"
OUT = ROOT / "data/derived/cutoff_blacklist_gap.json"

TOPICS = {
    "0x42e160154868087d6bfdc0ca23d96a1c1cfa32f1b72ba9ba27b69b98a0d819dc": ("USDT", "freezing", "data"),
    "0xd7e9ec6e6ecd65492dce6bf513cd6867560d49544421d0783ddf06e76c24470c": ("USDT", "unfreezing", "data"),
    "0x61e6e66b0d6339b2980aecc6ccc0039736791f0ccde9ed512e789a7fbdd698c6": ("USDT", "destruction", "data"),
    "0xffa4e6181777692565cf28528fc88fd1516ea86b56da075235fa575af6a4b855": ("USDC", "freezing", "topic"),
    "0x117e3210bb9aa7d9baff172026820255c6f6c30ba8999d1c2fd88e2848137c4e": ("USDC", "unfreezing", "topic"),
}
CONTRACTS = {
    "USDT": "0xdac17f958d2ee523a2206206994597c13d831ec7",
    "USDC": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
}


def decode_address(log: dict, kind: str) -> str:
    if kind == "topic":
        return "0x" + log["topics"][1][-40:].lower()
    return "0x" + log["data"][2:66][-40:].lower()


def main() -> None:
    snapshot = json.loads((ROOT / "data/raw/logs/ethereum_blacklist_logs.json").read_text(encoding="utf-8"))
    cutoff = int(json.loads((ROOT / "config/study_scope.json").read_text(encoding="utf-8"))["study_cutoff_block"])

    rows = []
    for log in snapshot["logs"]:
        token, event_class, kind = TOPICS[log["topics"][0].lower()]
        rows.append(
            (
                int(log["blockNumber"], 16),
                int(log["transactionIndex"], 16),
                int(log["logIndex"], 16),
                token,
                event_class,
                decode_address(log, kind),
            )
        )
    rows.sort()

    state: dict[tuple[str, str], str] = {}
    first_freeze: dict[tuple[str, str], int] = {}
    for block, _, _, token, event_class, address in rows:
        key = (token, address)
        if event_class == "freezing":
            state[key] = "blacklisted"
            first_freeze.setdefault(key, block)
        elif event_class == "unfreezing":
            state[key] = "not_blacklisted"
        elif key not in state:
            state[key] = "blacklisted"
            first_freeze.setdefault(key, block)

    still = {key for key, value in state.items() if value == "blacklisted"}
    balances = pd.read_csv(CORE / "balances.csv")
    cutoff_rows = balances[balances.balance_reference == "at_study_cutoff"]
    delivered = set(zip(cutoff_rows.token_symbol, cutoff_rows.address))
    missing = sorted(still - delivered)

    cfg = load_config()
    rpc = RpcCache(cfg)
    details = []
    for token, address in missing:
        contract = CONTRACTS[token]
        signature = cfg.tokens[token]["blacklist_state_function"]
        state_call = rpc.state_call(
            to=contract,
            data=encode_address_call(signature, address),
            block_number=cutoff,
            label="audit_state_at_cutoff",
        )
        balance_call = rpc.state_call(
            to=contract,
            data=encode_address_call("balanceOf(address)", address),
            block_number=cutoff,
            label="audit_balance_at_cutoff",
        )
        blacklisted = word_to_int(state_call.result.removeprefix("0x")) != 0
        balance_raw = word_to_int(balance_call.result.removeprefix("0x"))
        details.append(
            {
                "token_symbol": token,
                "address": address,
                "first_freeze_block": first_freeze[(token, address)],
                "blacklisted_at_cutoff": blacklisted,
                "balance_at_cutoff_raw": str(balance_raw),
                "balance_at_cutoff_token": str(balance_raw / 10 ** int(cfg.tokens[token]["decimals"])),
                "state_query_id": state_call.query_id,
                "balance_query_id": balance_call.query_id,
            }
        )

    payload = {
        "cutoff_block": cutoff,
        "reconstructed_blacklisted_addresses": len(still),
        "delivered_at_study_cutoff_rows": len(delivered),
        "missing_addresses": len(missing),
        "missing_total_balance_token": sum(float(d["balance_at_cutoff_token"]) for d in details),
        "all_reported_blacklisted": all(d["blacklisted_at_cutoff"] for d in details),
        "details": details,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in payload.items() if k != "details"}, ensure_ascii=False, indent=2))
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
