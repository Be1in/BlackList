"""Table contract: column order comes from ``docs/data-dictionary.csv``."""

from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

from .rpc import canonical_json, sha256_hex

# Provenance timestamps are the only fields allowed to differ between a live run
# and a replayed run; they are excluded from the data digest.
PROVENANCE_TIME_FIELDS: dict[str, set[str]] = {
    "events": {"source_retrieved_at_utc"},
    "balances": {"queried_at_utc"},
    "validation": {"reference_retrieved_at_utc", "reviewed_at_utc"},
    "matches": {"matched_at_utc"},
}


def read_table(path: Path) -> list[dict]:
    """Read a delivered CSV back into rows of strings (no type coercion)."""
    with path.open(encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def ratio(numerator: int, denominator: int) -> str:
    """A share fixed at six decimals; only call it when the denominator is non-zero."""
    if denominator == 0:
        return "0.000000"
    return format(Decimal(numerator) / Decimal(denominator), ".6f")


def share(numerator: int, denominator: int) -> str | None:
    """A share that returns ``None`` when nothing could be counted.

    A ratio over an empty denominator is not zero: it is unmeasured. Callers that
    must produce a string use :func:`ratio`; anything user-facing uses this.
    """
    return None if denominator == 0 else ratio(numerator, denominator)


def load_dictionary(path: Path) -> dict[str, list[str]]:
    columns: dict[str, list[str]] = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            table = row["table"].strip()
            field = row["field"].strip()
            if not table or not field:
                continue
            columns.setdefault(table, []).append(field)
    return columns


def assert_columns(table: str, fields: list[str], rows: list[dict]) -> None:
    if not fields:
        raise ValueError(f"{table}: 字典中没有该表的字段清单")
    for row in rows:
        extra = set(row) - set(fields)
        missing = set(fields) - set(row)
        if extra or missing:
            raise ValueError(
                f"{table}: 列集合与字典不一致 extra={sorted(extra)} missing={sorted(missing)}"
            )
        non_string = [key for key, value in row.items() if not isinstance(value, str)]
        if non_string:
            raise ValueError(f"{table}: 非字符串值 {sorted(non_string)}")


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row[field] for field in fields})


def write_parquet(path: Path, fields: list[str], rows: list[dict]) -> bool:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError:
        return False
    table = pa.Table.from_pylist(
        [{field: row[field] for field in fields} for row in rows],
        schema=pa.schema([(field, pa.string()) for field in fields]),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path, compression="snappy")
    return True


def write_bundle(
    out_dir: Path, dictionary: dict[str, list[str]], bundle: dict[str, list[dict]]
) -> tuple[list[Path], dict[str, bool]]:
    """Write every table of a bundle; CSV always, parquet when pyarrow is present."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    parquet_written: dict[str, bool] = {}
    for table, rows in bundle.items():
        csv_path = out_dir / f"{table}.csv"
        write_csv(csv_path, dictionary[table], rows)
        written.append(csv_path)
        parquet_written[table] = write_parquet(
            out_dir / f"{table}.parquet", dictionary[table], rows
        )
    return written, parquet_written


def data_digest(bundle: dict[str, list[dict]]) -> str:
    payload: dict[str, list[dict]] = {}
    for table, rows in bundle.items():
        dropped = PROVENANCE_TIME_FIELDS.get(table, set())
        payload[table] = [
            {key: value for key, value in row.items() if key not in dropped}
            for row in rows
        ]
    return sha256_hex(canonical_json(payload).encode())


def artifact_digest(paths: list[Path]) -> str:
    parts = []
    for path in sorted(paths):
        parts.append(path.name + ":" + sha256_hex(path.read_bytes()))
    return sha256_hex("|".join(parts).encode())
