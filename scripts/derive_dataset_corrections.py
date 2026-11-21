"""Derive the two correction views for the archived v1 dataset (v1 → v2 diff).

1. `d2_population_full.csv` — every address still blacklisted at the study
   cutoff (definitions.md 3.3), including the 29 addresses frozen before the
   observation window that the event-driven v1 path did not reach.
2. `event_state_corrections.csv` — the 16 destruction rows whose
   `blacklist_state_before` was taken at the block boundary rather than at the
   log position.

Both views are read-only derivations: the delivered v1 batch is not modified.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / ".backup/20261117_v1_replaced/core_64c8c7f9452291663926113a2b1ecc20"
GAP = ROOT / "data/derived/cutoff_blacklist_gap.json"
OUT_D2 = ROOT / "data/derived/d2_population_full.csv"
OUT_FIX = ROOT / "data/derived/event_state_corrections.csv"

TOPICS = {
    "0x42e160154868087d6bfdc0ca23d96a1c1cfa32f1b72ba9ba27b69b98a0d819dc": ("USDT", "freezing", "data"),
    "0xd7e9ec6e6ecd65492dce6bf513cd6867560d49544421d0783ddf06e76c24470c": ("USDT", "unfreezing", "data"),
    "0x61e6e66b0d6339b2980aecc6ccc0039736791f0ccde9ed512e789a7fbdd698c6": ("USDT", "destruction", "data"),
    "0xffa4e6181777692565cf28528fc88fd1516ea86b56da075235fa575af6a4b855": ("USDC", "freezing", "topic"),
    "0x117e3210bb9aa7d9baff172026820255c6f6c30ba8999d1c2fd88e2848137c4e": ("USDC", "unfreezing", "topic"),
}


def history(snapshot: dict) -> tuple[dict[tuple[str, str], str], dict[tuple[str, str], tuple[int, str]]]:
    state: dict[tuple[str, str], str] = {}
    first: dict[tuple[str, str], tuple[int, str]] = {}
    ordered = sorted(
        snapshot["logs"],
        key=lambda log: (
            int(log["blockNumber"], 16),
            int(log["transactionIndex"], 16),
            int(log["logIndex"], 16),
        ),
    )
    for log in ordered:
        token, event_class, decode = TOPICS[log["topics"][0].lower()]
        address = (
            "0x" + log["topics"][1][-40:].lower()
            if decode == "topic"
            else "0x" + log["data"][2:66][-40:].lower()
        )
        key = (token, address)
        block = int(log["blockNumber"], 16)
        stamp = pd.to_datetime(int(log["blockTimestamp"], 16), unit="s", utc=True).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        if event_class == "freezing":
            state[key] = "blacklisted"
            first.setdefault(key, (block, stamp))
        elif event_class == "unfreezing":
            state[key] = "not_blacklisted"
        elif key not in state:
            state[key] = "blacklisted"
            first.setdefault(key, (block, stamp))
    return state, first


def main() -> None:
    snapshot = json.loads((ROOT / "data/raw/logs/ethereum_blacklist_logs.json").read_text(encoding="utf-8"))
    state, first = history(snapshot)
    gap = json.loads(GAP.read_text(encoding="utf-8"))

    balances = pd.read_csv(CORE / "balances.csv")
    cutoff = balances[balances.balance_reference == "at_study_cutoff"].copy()
    cutoff["source"] = "core_batch"
    cutoff["in_core_batch"] = True
    cutoff = cutoff.rename(
        columns={"balance_raw": "balance_at_cutoff_raw", "balance_token": "balance_at_cutoff_token"}
    )[["token_symbol", "address", "balance_at_cutoff_raw", "balance_at_cutoff_token", "source", "in_core_batch"]]

    extra = pd.DataFrame(gap["details"])[
        ["token_symbol", "address", "balance_at_cutoff_raw", "balance_at_cutoff_token"]
    ].copy()
    extra["source"] = "audit_query_2026_11_17"
    extra["in_core_batch"] = False

    full = pd.concat([cutoff, extra], ignore_index=True)
    full["first_freeze_block"] = [
        first[(token, address)][0] for token, address in zip(full.token_symbol, full.address)
    ]
    full["first_freeze_time_utc"] = [
        first[(token, address)][1] for token, address in zip(full.token_symbol, full.address)
    ]
    full["still_blacklisted_at_cutoff"] = [
        state[(token, address)] == "blacklisted"
        for token, address in zip(full.token_symbol, full.address)
    ]
    full = full.sort_values(["token_symbol", "address"]).reset_index(drop=True)
    full.to_csv(OUT_D2, index=False)

    events = pd.read_csv(CORE / "events.csv")
    destruction = events[
        (events.event_class == "destruction") & (events.blacklist_transition)
    ].copy()
    rows = []
    for row in destruction.itertuples():
        prior = events[
            (events.token_symbol == row.token_symbol)
            & (events.affected_address == row.affected_address)
            & (events.block_number == row.block_number)
            & (events.log_index < row.log_index)
            & (events.event_class == "freezing")
        ].sort_values("log_index")
        evidence = prior.iloc[-1] if len(prior) else None
        rows.append(
            {
                "event_id": row.event_id,
                "token_symbol": row.token_symbol,
                "block_number": row.block_number,
                "log_index": row.log_index,
                "affected_address": row.affected_address,
                "delivered_state_before": row.blacklist_state_before,
                "delivered_state_after": row.blacklist_state_after,
                "delivered_transition": row.blacklist_transition,
                "corrected_state_before": "blacklisted",
                "corrected_state_after": "blacklisted",
                "corrected_transition": False,
                "evidence_event_id": evidence.event_id if evidence is not None else "",
                "evidence_log_index": int(evidence.log_index) if evidence is not None else "",
                "reason": (
                    "同一区块内更早的 freezing 已将该地址置为 blacklisted；"
                    "destruction 不改变黑名单状态位（definitions.md 2.3），"
                    "block-1 的 before 属于区块边界状态而非事件位置状态"
                ),
            }
        )
    pd.DataFrame(rows).to_csv(OUT_FIX, index=False)

    print(f"D2 population: {len(full)} rows ({full.in_core_batch.sum()} core + {(~full.in_core_batch).sum()} audit)")
    summary = (
        full.assign(balance_at_cutoff_token=pd.to_numeric(full.balance_at_cutoff_token, errors="coerce"))
        .groupby("token_symbol")
        .balance_at_cutoff_token.agg(["count", "sum"])
    )
    print(summary.to_string())
    print(f"state corrections: {len(rows)} rows")


if __name__ == "__main__":
    main()
