"""Add business names back to an answer, locally, after the model has written it (D-036, D-037).

The data tools return customer IDs only, and the model is told to refer to customers by ID. Before an answer
is shown, this code replaces each customer ID with "Business name (ID)", using dim_customer in the local
warehouse. Names never go to the model and are never produced by it.
"""
from __future__ import annotations

import re
from pathlib import Path

import duckdb

CUSTOMER_ID = re.compile(r"\bACC-\d{4}\b")


def lookup(warehouse_db: Path) -> dict[str, str]:
    con = duckdb.connect(str(warehouse_db), read_only=True)
    try:
        return dict(con.execute("SELECT customer_id, company_name FROM dim_customer "
                                "WHERE company_name IS NOT NULL").fetchall())
    finally:
        con.close()


def add_names(text: str, names: dict[str, str]) -> str:
    """'ACC-0027 is at risk' -> 'Example Ltd (ACC-0027) is at risk'. Unknown IDs are left as they are."""
    return CUSTOMER_ID.sub(lambda m: f"{names[m.group(0)]} ({m.group(0)})" if m.group(0) in names else m.group(0), text)
