"""Run all connectors and write the staging tables.

Run:  python -m pipeline.ingest.run   (the shop API must be running: see shop_api/app.py)

Output: data/staging/*.csv - one text-only CSV per source table, every value exactly as
exported plus lineage columns, and rejected_rows.csv. Running it twice gives identical files.
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx

from pipeline.ingest import schemas
from pipeline.ingest.common import LINEAGE, Rejected, write_staging
from pipeline.ingest.files import read_csv, read_excel_sheet
from pipeline.ingest.kb import KB_COLUMNS, read_kb
from pipeline.ingest.shop_api import LINE_COLUMNS, ORDER_COLUMNS, ShopApiUnavailable, fetch_orders
from pipeline.settings import load_settings

REJECTED_COLUMNS = ["source_system", "source_file", "source_row", "reason", "content"]


def ingest(settings: dict, client: httpx.Client | None = None, raw_dir: Path | None = None,
           kb_dir: Path | None = None, staging_dir: Path | None = None) -> dict[str, int]:
    raw = raw_dir or settings["raw_dir"]
    kb = kb_dir or settings["kb_dir"]
    out = staging_dir or settings["staging_dir"]
    at = settings["as_of_moment"]
    rejected = Rejected()
    tables = {}
    tables["crm_accounts"] = (read_excel_sheet(raw / "crm_accounts.xlsx", "Accounts", schemas.CRM_ACCOUNTS,
                                               "crm", at, rejected), schemas.CRM_ACCOUNTS)
    tables["crm_contract_lines"] = (read_excel_sheet(raw / "crm_accounts.xlsx", "Contract Lines", schemas.CRM_LINES,
                                                     "crm", at, rejected), schemas.CRM_LINES)
    tables["helpdesk_tickets"] = (read_csv(raw / "helpdesk_tickets.csv", schemas.HELPDESK, "helpdesk", at, rejected),
                                  schemas.HELPDESK)
    tables["telecoms_contracts"] = (read_csv(raw / "telecoms_contracts.csv", schemas.TELECOMS_CONTRACTS,
                                             "telecoms", at, rejected), schemas.TELECOMS_CONTRACTS)
    tables["telecoms_bills"] = (read_csv(raw / "telecoms_bills.csv", schemas.TELECOMS_BILLS, "telecoms", at,
                                         rejected), schemas.TELECOMS_BILLS)
    api = settings["shop_api"]
    own_client = client is None
    client = client or httpx.Client(timeout=api["timeout_seconds"])
    try:
        orders, lines, _ = fetch_orders(client, api["base_url"], api["page_size"], at, rejected)
    finally:
        if own_client:
            client.close()
    tables["shop_orders"] = (orders, ORDER_COLUMNS)
    tables["shop_order_lines"] = (lines, LINE_COLUMNS)
    tables["kb_documents"] = (read_kb(kb, at, rejected), KB_COLUMNS)

    counts = {}
    for name, (rows, cols) in tables.items():
        cols = cols if cols[-len(LINEAGE):] == LINEAGE else cols + LINEAGE
        write_staging(rows, cols, out / f"{name}.csv")
        counts[name] = len(rows)
    write_staging(rejected.rows, REJECTED_COLUMNS, out / "rejected_rows.csv")
    counts["rejected_rows"] = len(rejected.rows)
    return counts


def main() -> None:
    settings = load_settings()
    try:
        counts = ingest(settings)
    except ShopApiUnavailable as exc:
        print(f"Ingestion stopped: {exc}", file=sys.stderr)
        sys.exit(2)
    print(f"Staging tables written to {settings['staging_dir']}:")
    for name, n in counts.items():
        print(f"  {name}: {n}")


if __name__ == "__main__":
    main()
