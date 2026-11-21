"""Parse the official OFAC SDN Advanced XML into an address/date extract.

The frozen dataset needs one thing from this file: the *entity-level* designation
date that the SDN.CSV snapshot does not carry. Everything else (the address-string
membership test) stays on the pinned SDN snapshot, so this parse deliberately keeps
only what the date field needs:

* ``address``                 crypto address string, as published
* ``chain``                   feature-type suffix (ETH, USDT, TRX, ...), a label only
* ``entity``                  primary documented name of the profile
* ``ofac_programs``           SanctionsMeasure comments, joined with ``; ``
* ``entity_designation_date`` earliest ``SanctionsEntry/EntryEvent/Date`` of the profile
* ``sdn_profile_id``          ProfileID, the join key back to the XML
* ``source``                  file name + sha256 prefix

Reproduce:

    python scripts/parse_ofac_advanced.py \
        --xml data/raw/ofac/advanced/sdn_advanced.xml \
        --out data/raw/ofac/advanced/ofac_crypto_wallets_2026-11-13.csv

The parse is streaming (``iterparse`` + ``clear``); the 123 MB file is never held in
memory. A malformed profile or a missing date is skipped, never imputed.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NS = "{https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/ADVANCED_XML}"
FIELDS = [
    "address",
    "chain",
    "entity",
    "ofac_programs",
    "entity_designation_date",
    "sdn_profile_id",
    "source",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def profile_name(profile: ET.Element) -> str:
    """Primary identity -> primary alias -> documented name parts, in order."""
    identity = None
    for candidate in profile.findall(f"{NS}Identity"):
        if candidate.get("Primary") == "true":
            identity = candidate
            break
    if identity is None:
        identity = profile.find(f"{NS}Identity")
    if identity is None:
        return ""
    alias = None
    for candidate in identity.findall(f"{NS}Alias"):
        if candidate.get("Primary") == "true":
            alias = candidate
            break
    if alias is None:
        alias = identity.find(f"{NS}Alias")
    if alias is None:
        return ""
    document = alias.find(f"{NS}DocumentedName")
    if document is None:
        return ""
    parts = [(part.text or "").strip() for part in document.iter(f"{NS}NamePartValue")]
    return " ".join(part for part in parts if part)


def entry_date(entry: ET.Element) -> str:
    """Earliest EntryEvent date; later re-designations do not move the original one."""
    dates: list[date] = []
    for event in entry.findall(f"{NS}EntryEvent"):
        year = (event.findtext(f"{NS}Date/{NS}Year") or "").strip()
        month = (event.findtext(f"{NS}Date/{NS}Month") or "").strip()
        day = (event.findtext(f"{NS}Date/{NS}Day") or "").strip()
        if not (year and month and day):
            continue
        try:
            dates.append(date(int(year), int(month), int(day)))
        except ValueError:
            continue
    return min(dates).isoformat() if dates else ""


def parse(xml_path: Path, out_path: Path) -> dict:
    feature_names: dict[str, str] = {}
    profiles: dict[str, dict] = {}
    entries: dict[str, dict] = {}
    issue = ""

    for _, elem in ET.iterparse(xml_path, events=("end",)):
        tag = elem.tag
        if tag == f"{NS}DateOfIssue":
            year = (elem.findtext(f"{NS}Year") or "").strip()
            month = (elem.findtext(f"{NS}Month") or "").strip()
            day = (elem.findtext(f"{NS}Day") or "").strip()
            if year and month and day:
                issue = f"{year}-{int(month):02d}-{int(day):02d}"
            elem.clear()
        elif tag == f"{NS}FeatureType":
            feature_names[elem.get("ID", "")] = (elem.text or "").strip()
            elem.clear()
        elif tag == f"{NS}Profile":
            profile_id = elem.get("ID", "")
            addresses: list[tuple[str, str]] = []
            for feature in elem.findall(f"{NS}Feature"):
                label = feature_names.get(feature.get("FeatureTypeID", ""), "")
                if not label.startswith("Digital Currency Address"):
                    continue
                chain = label.rsplit("-", 1)[-1].strip()
                for detail in feature.iter(f"{NS}VersionDetail"):
                    value = (detail.text or "").strip()
                    if value:
                        addresses.append((chain, value))
            profiles[profile_id] = {"name": profile_name(elem), "addresses": addresses}
            elem.clear()
        elif tag == f"{NS}SanctionsEntry":
            profile_id = elem.get("ProfileID", "")
            programs = sorted(
                {
                    (comment.text or "").strip()
                    for comment in elem.iter(f"{NS}Comment")
                    if (comment.text or "").strip()
                }
            )
            entries[profile_id] = {
                "date": entry_date(elem),
                "programs": "; ".join(programs),
            }
            elem.clear()

    source_sha = sha256(xml_path)
    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for profile_id, profile in profiles.items():
        entry = entries.get(profile_id, {})
        for chain, address in profile["addresses"]:
            key = (address.lower(), profile_id)
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "address": address,
                    "chain": chain,
                    "entity": profile["name"],
                    "ofac_programs": entry.get("programs", ""),
                    "entity_designation_date": entry.get("date", ""),
                    "sdn_profile_id": profile_id,
                    "source": f"sdn_advanced.xml@{source_sha[:12]}",
                }
            )
    rows.sort(key=lambda row: (row["address"].lower(), row["chain"]))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    eth = [row for row in rows if row["address"].startswith("0x")]
    summary = {
        "source_xml": str(xml_path.relative_to(ROOT)).replace("\\", "/"),
        "source_xml_sha256": source_sha,
        "source_date_of_issue": issue,
        "output_csv": str(out_path.relative_to(ROOT)).replace("\\", "/"),
        "output_csv_sha256": sha256(out_path),
        "profiles_with_entries": len(entries),
        "profiles_with_addresses": len(profiles),
        "rows": len(rows),
        "eth_rows": len(eth),
        "eth_rows_without_date": sum(1 for row in eth if not row["entity_designation_date"]),
        "eth_unique_addresses": len({row["address"].lower() for row in eth}),
    }
    manifest = out_path.with_name(out_path.stem + "_manifest.json")
    manifest.write_text(
        json.dumps(summary, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--xml",
        default=str(ROOT / "data/raw/ofac/advanced/sdn_advanced.xml"),
        help="SDN Advanced XML as downloaded from OFAC",
    )
    parser.add_argument(
        "--out",
        default=str(ROOT / "data/raw/ofac/advanced/ofac_crypto_wallets_2026-11-13.csv"),
        help="address/date extract to write",
    )
    args = parser.parse_args()
    summary = parse(Path(args.xml), Path(args.out))
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
