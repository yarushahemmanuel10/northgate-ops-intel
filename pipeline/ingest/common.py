"""Shared pieces for the connectors: schemas, lineage, rejected rows, staging output.

Ingestion only rejects rows whose structure cannot be read (wrong number of columns, a
record that is not valid JSON, a missing required field). Quality problems such as odd
dates, invalid priorities or name variants pass through unchanged; validation (step 2.2)
deals with them (D-017).
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

MARKER = "# SYNTHETIC DATA"
LINEAGE = ["_source_system", "_source_file", "_source_row", "_ingested_at"]


class SchemaError(ValueError):
    """The file's structure is not what the connector expects; ingestion stops."""


@dataclass
class Rejected:
    rows: list[dict] = field(default_factory=list)

    def add(self, system: str, file: str, row: str, reason: str, content: str) -> None:
        self.rows.append({"source_system": system, "source_file": file, "source_row": row,
                          "reason": reason, "content": content[:500]})


def check_header(found: list[str], expected: list[str], where: str) -> None:
    missing = [c for c in expected if c not in found]
    extra = [c for c in found if c not in expected]
    if missing or extra:
        raise SchemaError(f"{where}: unexpected columns. Missing: {missing or 'none'}; "
                          f"unexpected: {extra or 'none'}; expected exactly {expected}")
    if found != expected:
        raise SchemaError(f"{where}: columns in an unexpected order: {found}")


def write_staging(rows: list[dict], columns: list[str], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in columns})
