"""Build a deterministic, stratified human sign-off sheet.

The sheet is a convenience layer over the delivered tables: it selects events
across issuer × class and adds cutoff-balance rows, then leaves reviewer fields
blank. It does not change any delivered value.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "data/recon/core_64c8c7f9452291663926113a2b1ecc20"
OUT = ROOT / "data/derived/manual_signoff_sample.csv"


def pick(group: pd.DataFrame, n: int = 4) -> pd.DataFrame:
    group = group.sort_values("block_number").reset_index(drop=True)
    if len(group) <= n:
        return group
    positions = sorted({round(i * (len(group) - 1) / (n - 1)) for i in range(n)})
    return group.iloc[positions]


def main() -> None:
    events = pd.read_csv(CORE / "events.csv")
    balances = pd.read_csv(CORE / "balances.csv")

    rows = []
    for (issuer, event_class), group in events.groupby(["token_symbol", "event_class"]):
        for event in pick(group, 4).itertuples():
            balance_ref = "at_freeze" if event.event_class == "freezing" else "at_event"
            balance_row = balances[
                (balances.reference_event_id == event.event_id)
                & (balances.balance_reference == balance_ref)
            ]
            balance = (
                balance_row.balance_token.iloc[0]
                if len(balance_row)
                else event.balance_pre_event_token
            )
            rows.append(
                {
                    "row_type": "event",
                    "event_id": event.event_id,
                    "token_symbol": issuer,
                    "event_class": event_class,
                    "block_number": event.block_number,
                    "tx_hash": event.tx_hash,
                    "log_index": event.log_index,
                    "affected_address": event.affected_address,
                    "state_before": event.blacklist_state_before,
                    "state_after": event.blacklist_state_after,
                    "transition": event.blacklist_transition,
                    "balance_reference": balance_ref,
                    "balance_token": balance,
                    "etherscan_url": f"https://etherscan.io/tx/{event.tx_hash}#eventlog",
                    "address_url": f"https://etherscan.io/address/{event.affected_address}",
                    "reviewer": "",
                    "reviewed_at_utc": "",
                    "decision": "",
                    "note": "",
                }
            )

    cutoff = balances[balances.balance_reference == "at_study_cutoff"].copy()
    cutoff = cutoff.sort_values(["token_symbol", "balance_token"], ascending=[True, False])
    sample = pd.concat(
        [
            cutoff[cutoff.token_symbol == "USDT"].head(3),
            cutoff[cutoff.token_symbol == "USDC"].head(3),
        ]
    )
    for row in sample.itertuples():
        rows.append(
            {
                "row_type": "d2",
                "event_id": row.reference_event_id,
                "token_symbol": row.token_symbol,
                "event_class": "at_study_cutoff",
                "block_number": row.balance_block_number,
                "tx_hash": "",
                "log_index": "",
                "affected_address": row.address,
                "state_before": row.blacklist_state_at_balance_block,
                "state_after": row.blacklist_state_at_balance_block,
                "transition": "",
                "balance_reference": "at_study_cutoff",
                "balance_token": row.balance_token,
                "etherscan_url": "",
                "address_url": f"https://etherscan.io/address/{row.address}",
                "reviewer": "",
                "reviewed_at_utc": "",
                "decision": "",
                "note": "",
            }
        )

    frame = pd.DataFrame(rows)
    frame.insert(0, "sheet_row", range(2, len(frame) + 2))
    frame.to_csv(OUT, index=False)
    print(f"wrote {OUT.relative_to(ROOT)}: {len(frame)} rows")
    print(frame.groupby(["row_type", "token_symbol", "event_class"]).size().to_string())


if __name__ == "__main__":
    main()
