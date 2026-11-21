"""Audit the delivered freeze-event dataset against its declared contract.

Read-only. Every check is derived from the delivered tables, the data
dictionary and the batch manifests; nothing is repaired here. The output is a
machine-readable list of findings plus a short human summary.

Usage:
    python scripts/audit_dataset.py [--write]
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORE = ROOT / "data/recon/core_64c8c7f9452291663926113a2b1ecc20"
DEFAULT_MATCH = ROOT / "data/recon/match_e7a32e2a4120dccb5d5cfe1edf360766"
DICT = ROOT / "docs/data-dictionary.csv"
OUT = ROOT / "data/derived/dataset_audit.json"

parser = argparse.ArgumentParser()
parser.add_argument("--core-dir", default=str(DEFAULT_CORE))
parser.add_argument("--match-dir", default=str(DEFAULT_MATCH))
parser.add_argument("--out", default=str(OUT))
parser.add_argument("--write", action="store_true")
args = parser.parse_args()
CORE = Path(args.core_dir)
MATCH = Path(args.match_dir)

CHECKS: list[dict] = []


def record(check_id: str, severity: str, ok: bool, detail: str) -> None:
    CHECKS.append(
        {
            "id": check_id,
            "severity": severity,
            "status": "pass" if ok else "fail",
            "detail": detail,
        }
    )


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    events = pd.read_csv(CORE / "events.csv")
    balances = pd.read_csv(CORE / "balances.csv")
    validation = pd.read_csv(CORE / "validation.csv")
    registry = pd.read_csv(CORE / "chain_registry.csv")
    matches = pd.read_csv(MATCH / "matches.csv")
    dictionary = pd.read_csv(DICT)
    core_manifest = json.loads((CORE / "manifest.json").read_text(encoding="utf-8"))
    match_manifest = json.loads((MATCH / "manifest.json").read_text(encoding="utf-8"))
    scope = json.loads((ROOT / "config/study_scope.json").read_text(encoding="utf-8"))

    start_block = int(scope["observation_start_block"])
    cutoff_block = int(scope["study_cutoff_block"])

    # ---------- F: files and manifests ----------
    for table in ("events", "balances", "validation"):
        csv_df = pd.read_csv(CORE / f"{table}.csv")
        pq_df = pd.read_parquet(CORE / f"{table}.parquet")
        record(
            f"F1-{table}",
            "high",
            csv_df.shape == pq_df.shape and list(csv_df.columns) == list(pq_df.columns),
            f"{table}: csv {csv_df.shape} vs parquet {pq_df.shape}",
        )

    counts = core_manifest["counts"]
    actual_counts = {
        "events": len(events),
        "balances": len(balances),
        "validation": len(validation),
        "chain_registry": len(registry),
    }
    record(
        "F2-core-counts",
        "high",
        all(int(counts[k]) == v for k, v in actual_counts.items()),
        f"manifest {actual_counts} vs declared { {k: counts[k] for k in actual_counts} }",
    )
    record(
        "F2-match-counts",
        "high",
        int(match_manifest["counts"]["matches"]) == len(matches),
        f"manifest {match_manifest['counts']['matches']} vs actual {len(matches)}",
    )

    record(
        "F3-log-snapshot-digest",
        "high",
        sha256(ROOT / core_manifest["inputs"]["log_snapshot"]) == core_manifest["inputs"]["log_snapshot_sha256"],
        "raw log snapshot sha256 matches manifest",
    )
    record(
        "F3-scope-digest",
        "high",
        sha256(ROOT / core_manifest["inputs"]["scope_decision"]) == core_manifest["inputs"]["scope_decision_sha256"],
        "study scope sha256 matches manifest",
    )
    record(
        "F4-dictionary-path",
        "medium",
        core_manifest["inputs"].get("dictionary") == "docs/data-dictionary.csv",
        f"manifest dictionary path: {core_manifest['inputs'].get('dictionary')}",
    )

    # ---------- E: events ----------
    record("E1-event-id-unique", "high", events.event_id.is_unique, f"{events.event_id.nunique()} unique / {len(events)} rows")

    derived_id = (
        "ev_"
        + (
            events["chain"].astype(str)
            + "|"
            + events["tx_hash"].astype(str)
            + "|"
            + events["log_index"].astype(str)
        ).map(lambda s: hashlib.sha256(s.encode()).hexdigest()[:32])
    )
    record(
        "E2-event-id-derivation",
        "high",
        bool((derived_id == events.event_id).all()),
        f"{(derived_id == events.event_id).sum()}/{len(events)} match sha256(chain|tx|log_index)[:32]",
    )

    record("E3-log-order-key-unique", "high", events.log_order_key.is_unique, "log_order_key unique")
    expected_key = (
        events.block_number.map(lambda v: f"{int(v):010d}")
        + ":"
        + events.tx_index.map(lambda v: f"{int(v):04d}")
        + ":"
        + events.log_index.map(lambda v: f"{int(v):06d}")
    )
    record("E3-log-order-key-format", "high", bool((expected_key == events.log_order_key).all()), "key format block:tx:log")

    tx_sizes = events.groupby("tx_hash").size()
    declared = events.groupby("tx_hash").tx_event_count.first()
    record(
        "E4-tx-event-count",
        "medium",
        bool((tx_sizes == declared).all()),
        f"{(tx_sizes == declared).sum()}/{len(tx_sizes)} transactions match",
    )

    hash_per_block = events.groupby("block_number").block_hash.nunique()
    record("E5-one-hash-per-block", "high", bool((hash_per_block == 1).all()), f"max distinct hashes per block {hash_per_block.max()}")

    times = events.sort_values("block_number").block_time_utc.map(pd.Timestamp)
    monotone = bool((times.diff().dropna().dt.total_seconds() >= 0).all())
    record("E6-time-monotone", "high", monotone, "block time non-decreasing with block number")

    enums = {
        "token_symbol": {"USDT", "USDC"},
        "event_class": {"freezing", "unfreezing", "destruction"},
        "verification_status": {"verified", "unverifiable", "pending"},
        "analysis_eligibility": {"core", "core_unverifiable", "excluded"},
        "contract_version_confidence": {"high", "low"},
        "blacklist_state_before": {"blacklisted", "not_blacklisted"},
        "blacklist_state_after": {"blacklisted", "not_blacklisted"},
    }
    for column, allowed in enums.items():
        observed = set(events[column].dropna().unique())
        record(f"E7-enum-{column}", "high", observed <= allowed, f"observed {sorted(observed)}")

    required = dictionary[(dictionary.table == "events") & (dictionary.required == "yes")].field.tolist()
    missing_required = [c for c in required if c in events.columns and events[c].isna().any()]
    record(
        "E8-required-non-null",
        "high",
        not missing_required,
        f"required fields with nulls: {missing_required or 'none'}",
    )

    record(
        "E9-chain",
        "high",
        set(events.chain) == {"ethereum"} and set(events.chain_id) == {1},
        f"chains {sorted(set(events.chain))}, ids {sorted(set(events.chain_id))}",
    )
    record(
        "E10-scope-bounds",
        "high",
        bool(events.block_number.between(start_block, cutoff_block).all()),
        f"blocks {events.block_number.min()}..{events.block_number.max()} within {start_block}..{cutoff_block}",
    )

    destruction = events[events.event_class == "destruction"]
    bad_destruction = destruction[
        (destruction.blacklist_state_before != "blacklisted")
        | (destruction.blacklist_state_after != "blacklisted")
        | (destruction.blacklist_transition)
    ]
    record(
        "E11-destruction-state-contract",
        "high",
        len(bad_destruction) == 0,
        f"{len(bad_destruction)}/{len(destruction)} destruction rows violate 'before == after == blacklisted' "
        f"(blocks: {sorted(bad_destruction.block_number.tolist())[:5]}...)",
    )

    freezing = events[events.event_class == "freezing"]
    bad_freeze_true = freezing[
        freezing.blacklist_transition
        & ((freezing.blacklist_state_before != "not_blacklisted") | (freezing.blacklist_state_after != "blacklisted"))
    ]
    bad_freeze_false = freezing[
        ~freezing.blacklist_transition
        & (freezing.blacklist_state_before != freezing.blacklist_state_after)
    ]
    record(
        "E12-freezing-state-contract",
        "high",
        len(bad_freeze_true) == 0 and len(bad_freeze_false) == 0,
        f"transition=true violations {len(bad_freeze_true)}, transition=false violations {len(bad_freeze_false)}",
    )

    unfreezing = events[events.event_class == "unfreezing"]
    bad_uf_true = unfreezing[
        unfreezing.blacklist_transition
        & ((unfreezing.blacklist_state_before != "blacklisted") | (unfreezing.blacklist_state_after != "not_blacklisted"))
    ]
    bad_uf_false = unfreezing[
        ~unfreezing.blacklist_transition
        & (unfreezing.blacklist_state_before != unfreezing.blacklist_state_after)
    ]
    record(
        "E13-unfreezing-state-contract",
        "high",
        len(bad_uf_true) == 0 and len(bad_uf_false) == 0,
        f"transition=true violations {len(bad_uf_true)}, transition=false violations {len(bad_uf_false)}",
    )

    amount_missing = destruction.destruction_amount_token.isna().sum()
    non_destruction_amount = events[events.event_class != "destruction"].destruction_amount_token.notna().sum()
    record(
        "E14-destruction-amount-scope",
        "medium",
        amount_missing == 0 and non_destruction_amount == 0,
        f"missing on destruction {amount_missing}, present on non-destruction {non_destruction_amount}",
    )
    amount_vs_balance = destruction.dropna(subset=["destruction_amount_token", "balance_pre_event_token"])
    mismatch = amount_vs_balance[
        (amount_vs_balance.destruction_amount_token - amount_vs_balance.balance_pre_event_token).abs() > 0.000001
    ]
    record(
        "E14b-destruction-equals-prebalance",
        "medium",
        len(mismatch) == 0,
        f"{len(mismatch)}/{len(amount_vs_balance)} destruction amounts differ from pre-event balance",
    )

    low_conf = events[events.contract_version_confidence == "low"]
    record(
        "E15-low-confidence-versions",
        "medium",
        set(low_conf.token_symbol) <= {"USDC"},
        f"{len(low_conf)} low-confidence rows, issuers {sorted(set(low_conf.token_symbol))}",
    )

    starts = freezing[freezing.blacklist_transition]
    record(
        "E16-d1-present-on-starts",
        "high",
        starts.balance_pre_event_token.notna().all(),
        f"{starts.balance_pre_event_token.notna().sum()}/{len(starts)} episode starts carry D1",
    )
    r9 = freezing[~freezing.blacklist_transition]
    r9_in_d1 = set(r9.event_id) & set(
        balances.loc[balances.balance_reference == "at_freeze", "reference_event_id"].dropna()
    )
    record(
        "E16b-r9-not-in-d1",
        "medium",
        not r9_in_d1,
        f"{len(r9_in_d1)} R9 events carry an at_freeze balance row",
    )
    record(
        "E17-pre-event-block",
        "high",
        bool((events.balance_pre_event_state_block.dropna().astype(int) == events.loc[events.balance_pre_event_state_block.notna(), "block_number"] - 1).all()),
        "balance_pre_event_state_block == block_number - 1",
    )

    missing_status = events.missingness_status == "complete"
    record(
        "E18-missingness-status",
        "medium",
        bool((missing_status == (events.missing_fields.astype("string").fillna("") == "")).all()),
        "missingness_status consistent with missing_fields",
    )
    record(
        "E19-eligibility",
        "high",
        set(events.analysis_eligibility) == {"core"} and events.exclusion_rule_id.isna().all(),
        f"eligibility {sorted(set(events.analysis_eligibility))}, exclusions {events.exclusion_rule_id.notna().sum()}",
    )

    declared_class = core_manifest["counts"]["event_class"]
    actual_class = events.event_class.value_counts().to_dict()
    record(
        "E20-class-counts",
        "high",
        all(int(declared_class[k]) == actual_class[k] for k in actual_class),
        f"actual {actual_class}",
    )

    # ---------- B: balances ----------
    record("B1-query-id-unique", "high", balances.query_id.is_unique, f"{balances.query_id.nunique()} unique / {len(balances)}")
    known_events = set(events.event_id)
    linked = balances.dropna(subset=["reference_event_id"])
    record(
        "B2-reference-integrity",
        "high",
        set(linked.reference_event_id) <= known_events,
        f"{len(set(linked.reference_event_id) - known_events)} dangling references",
    )

    at_freeze = balances[balances.balance_reference == "at_freeze"]
    record(
        "B3-d1-row-count",
        "high",
        len(at_freeze) == len(starts),
        f"at_freeze {len(at_freeze)} vs episode starts {len(starts)}",
    )
    dup_freeze = at_freeze.groupby("reference_event_id").size()
    record(
        "B3b-one-d1-per-start",
        "high",
        bool((dup_freeze == 1).all()) and set(dup_freeze.index) == set(starts.event_id),
        f"events covered {len(dup_freeze)}, duplicates {int((dup_freeze > 1).sum())}",
    )
    event_block = events.set_index("event_id").block_number
    expected_block = at_freeze.reference_event_id.map(event_block) - 1
    record(
        "B4-d1-block",
        "high",
        bool((at_freeze.balance_block_number == expected_block).all()),
        "at_freeze queried at event block - 1",
    )
    record(
        "B4b-d1-state",
        "high",
        set(at_freeze.blacklist_state_at_balance_block) == {"not_blacklisted"},
        f"states {sorted(set(at_freeze.blacklist_state_at_balance_block))}",
    )

    at_cutoff = balances[balances.balance_reference == "at_study_cutoff"]
    record(
        "B5-d2-block-state",
        "high",
        bool((at_cutoff.balance_block_number == cutoff_block).all())
        and set(at_cutoff.blacklist_state_at_balance_block) == {"blacklisted"},
        f"rows {len(at_cutoff)}, blocks {sorted(set(at_cutoff.balance_block_number))}, states {sorted(set(at_cutoff.blacklist_state_at_balance_block))}",
    )
    record(
        "B5b-d2-unique-address",
        "high",
        not at_cutoff.duplicated(["token_symbol", "address"]).any(),
        f"duplicate address rows {int(at_cutoff.duplicated(['token_symbol', 'address']).sum())}",
    )

    # Reconstruct the full blacklist state from every snapshot log, including the
    # 90 pre-window events, so the D2 population can be checked against its
    # definition rather than against the in-window event set.
    snapshot = json.loads((ROOT / "data/raw/logs/ethereum_blacklist_logs.json").read_text(encoding="utf-8"))
    topic_map = {
        row.event_topic0: (row.token_symbol, row.event_class, row.address_decode_source)
        for row in events.itertuples()
    }
    history: dict[tuple[str, str], str] = {}
    ordered_logs = sorted(
        snapshot["logs"],
        key=lambda log: (
            int(log["blockNumber"], 16),
            int(log["transactionIndex"], 16),
            int(log["logIndex"], 16),
        ),
    )
    for log in ordered_logs:
        topic = log["topics"][0].lower()
        if topic not in topic_map:
            continue
        token, event_class, decode = topic_map[topic]
        address = (
            "0x" + log["topics"][1][-40:].lower()
            if decode == "topic"
            else "0x" + log["data"][2:66][-40:].lower()
        )
        key = (token, address)
        if event_class == "freezing":
            history[key] = "blacklisted"
        elif event_class == "unfreezing":
            history[key] = "not_blacklisted"
        elif key not in history:
            history[key] = "blacklisted"

    full_still = {key for key, value in history.items() if value == "blacklisted"}
    delivered = set(zip(at_cutoff.token_symbol, at_cutoff.address))
    record(
        "B6a-d2-subset-of-history",
        "high",
        delivered <= full_still,
        f"{len(delivered - full_still)} delivered rows are not blacklisted in full history",
    )
    record(
        "B6b-d2-population-complete",
        "high",
        full_still <= delivered,
        f"{len(full_still)} still blacklisted in full history, {len(delivered)} delivered; "
        f"{len(full_still - delivered)} missing (all pre-window freezes retained at cutoff)",
    )
    record(
        "B6c-d2-no-extras",
        "high",
        not (delivered - full_still),
        f"{len(delivered - full_still)} rows outside the defined population",
    )

    parsed = balances.dropna(subset=["balance_raw", "balance_token"])
    recomputed = parsed.balance_raw.astype(float) / (10 ** parsed.token_decimals)
    record(
        "B7-token-conversion",
        "medium",
        bool(((recomputed - parsed.balance_token.astype(float)).abs() < 0.000001).all()),
        f"{(recomputed - parsed.balance_token.astype(float)).abs().max()} max difference",
    )
    record(
        "B8-balance-source",
        "medium",
        set(balances.balance_source) == {"archive_eth_call"} and set(balances.missingness_status) == {"complete"},
        f"sources {sorted(set(balances.balance_source))}, missingness {sorted(set(balances.missingness_status))}",
    )

    # ---------- M: matches ----------
    per_event = matches.groupby("event_id").size()
    record(
        "M1-two-layers-per-event",
        "high",
        bool((per_event == 2).all()) and set(per_event.index) == known_events,
        f"events covered {len(per_event)}, events with !=2 rows {int((per_event != 2).sum())}",
    )
    record(
        "M2-match-reference",
        "high",
        set(matches.event_id) <= known_events,
        f"{len(set(matches.event_id) - known_events)} dangling",
    )
    exchange = matches[matches.match_layer == "exchange_label"]
    record(
        "M3-exchange-layer",
        "high",
        set(exchange.match_status) == {"not_attempted"}
        and set(exchange.missing_reason_code.dropna()) == {"external_source_unavailable"},
        f"statuses {sorted(set(exchange.match_status))}",
    )
    ofac = matches[matches.match_layer == "ofac_sdn"]
    matched = ofac[ofac.match_status == "matched"]
    unmatched = ofac[ofac.match_status == "unmatched"]
    record(
        "M4-ofac-evidence",
        "high",
        matched.matched_record_id.notna().all()
        and matched.match_evidence_address_string.notna().all()
        and unmatched.missing_reason_code.notna().all(),
        f"matched {len(matched)}, unmatched {len(unmatched)}",
    )
    dated = ofac[ofac.sanctions_public_timestamp.notna()]
    record(
        "M5-date-fields",
        "high",
        not dated.empty
        and set(dated.sanctions_date_basis.dropna()) <= {"designation_date"}
        and set(dated.sanctions_date_precision.dropna()) == {"day"}
        and set(matched.label_date_basis.dropna()) == {"list_snapshot_date"},
        f"sanctions dates {len(dated)} "
        f"(basis {sorted(set(dated.sanctions_date_basis.dropna()))}, "
        f"precision {sorted(set(dated.sanctions_date_precision.dropna()))}), "
        f"label dates {sorted(set(matched.label_date.dropna()))}",
    )
    # Every delivered date must trace back to the registered extract, row by row.
    date_registry = json.loads(
        (ROOT / "config/ofac_advanced.json").read_text(encoding="utf-8")
    )
    source_path = ROOT / date_registry["source"]["path"]
    source = pd.read_csv(source_path, encoding="utf-8-sig", dtype=str).fillna("")
    source = source[source.address.str.lower().str.startswith("0x")]
    source_dates = dict(
        zip(source.address.str.lower(), source.entity_designation_date, strict=False)
    )
    untraceable = [
        row.match_evidence_address_string
        for row in dated.itertuples()
        if source_dates.get(row.match_evidence_address_string.lower())
        != row.sanctions_public_timestamp
    ]
    record(
        "M6-date-provenance",
        "high",
        not untraceable,
        f"{len(untraceable)} dated rows do not trace back to {date_registry['source']['path']}",
    )

    # ---------- V: validation ----------
    record(
        "V1-agreement",
        "high",
        set(validation.agreement) == {"match"},
        f"agreements {validation.agreement.value_counts().to_dict()}",
    )
    record(
        "V2-check-types",
        "medium",
        set(validation.check_type) <= {"source_position", "order_version", "balance_state", "manual_reference", "schema_conformance"},
        f"types {sorted(set(validation.check_type))}",
    )
    record(
        "V3-reviewer",
        "medium",
        set(validation.reviewer) == {"agent:auto-cross-check"},
        f"reviewers {sorted(set(validation.reviewer))} (human sign-off pending)",
    )
    all_fields = set(dictionary.field)
    record(
        "V4-field-names",
        "medium",
        set(validation.field_name.dropna()) <= all_fields,
        f"{len(set(validation.field_name.dropna()) - all_fields)} fields outside dictionary",
    )

    # ---------- C: cross-batch ----------
    record(
        "C1-core-digest-link",
        "high",
        match_manifest["inputs"]["core_data_digest"] == core_manifest["digests"]["data_digest"],
        "match manifest points at current core data_digest",
    )
    record(
        "C2-chain-registry",
        "high",
        set(registry.chain) == {"ethereum", "tron"} and set(events.chain) == {"ethereum"},
        f"registered {sorted(set(registry.chain))}, events {sorted(set(events.chain))}",
    )

    # ---------- D: dictionary coverage ----------
    declared = {
        table: set(dictionary[dictionary.table == table].field) for table in dictionary.table.unique()
    }
    for table, frame in (("events", events), ("balances", balances), ("matches", matches), ("validation", validation), ("chain_registry", registry)):
        unknown = set(frame.columns) - declared.get(table, set())
        record(
            f"D1-{table}-columns",
            "high",
            not unknown,
            f"undeclared columns: {sorted(unknown) or 'none'}",
        )

    failures = [c for c in CHECKS if c["status"] == "fail"]
    summary = {
        "checks": len(CHECKS),
        "pass": len(CHECKS) - len(failures),
        "fail": len(failures),
        "high_severity_failures": [c["id"] for c in failures if c["severity"] == "high"],
        "all_failures": [c["id"] for c in failures],
    }
    payload = {
        "generated_at_utc": pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ"),
        "core_batch": CORE.name,
        "match_batch": MATCH.name,
        "summary": summary,
        "checks": CHECKS,
    }

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    for check in failures:
        print(f"[{check['severity']}] {check['id']}: {check['detail']}")

    if args.write:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote {out_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
