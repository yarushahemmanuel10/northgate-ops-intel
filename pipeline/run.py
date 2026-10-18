"""Run the whole pipeline in order (step 3.5, D-027).

Run:  python -m pipeline.run   (the shop API must be running, see README)

ingest -> validate -> resolve -> dedup -> load -> export -> alerts -> KB index, then a short summary: row counts,
revenue by line, the Unknown customer total, active alerts and the KB index size. If the shop API is not reachable it stops with
the same message as ingestion, before anything downstream is rebuilt.
"""
from __future__ import annotations

import math
import sys

import duckdb
import httpx

from pipeline.alerts import run as alerts
from pipeline.ingest.run import ingest
from pipeline.ingest.shop_api import ShopApiUnavailable
from pipeline.retrieval import index as kb_index
from pipeline.resolve.run import load_config as resolution_config, resolve
from pipeline.settings import load_settings
from pipeline.validate.run import load_rules, validate
from pipeline.warehouse import dedup, export
from pipeline.warehouse.load import build, load_config as warehouse_config


def run_all(settings: dict, client: httpx.Client | None = None) -> dict:
    wcfg = warehouse_config()
    ingest(settings, client=client)
    validate(settings, load_rules())
    resolve(settings, resolution_config())
    dedup.run(settings, wcfg)
    built = build(settings, wcfg)
    exported = export.run(settings, wcfg)
    raised = alerts.run(settings)
    indexed = kb_index.build(settings)
    return {"rows": built["rows"], "exported": exported, "alerts": raised, "kb_index": indexed,
            "summary": summary(settings["warehouse_dir"] / wcfg["database"])}


def summary(db) -> dict:
    con = duckdb.connect(str(db), read_only=True)
    try:
        rows = con.execute("SELECT business_line, revenue_basis, customer_id, revenue FROM v_revenue").fetchall()
        unknown_rows = con.execute(
            "SELECT (SELECT COUNT(*) FROM fact_ticket WHERE customer_id = 'UNKNOWN') + "
            "(SELECT COUNT(*) FROM fact_order WHERE customer_id = 'UNKNOWN') + "
            "(SELECT COUNT(*) FROM fact_bill WHERE customer_id = 'UNKNOWN')").fetchone()[0]
    finally:
        con.close()
    # math.fsum: the correctly rounded total whatever order the rows come in (D-027)
    lines = sorted({(bl, basis) for bl, basis, _, _ in rows})
    revenue = [(bl, basis, math.fsum(r[3] for r in rows if r[0] == bl)) for bl, basis in lines]
    unknown = math.fsum(r[3] for r in rows if r[2] == "UNKNOWN")
    return {"revenue": revenue, "unknown_revenue": unknown, "unknown_rows": unknown_rows}


def main() -> None:
    settings = load_settings()
    try:
        out = run_all(settings)
    except ShopApiUnavailable as exc:
        print(f"Ingestion stopped: {exc}", file=sys.stderr)
        sys.exit(2)
    print("Pipeline complete.")
    print("Warehouse rows:", ", ".join(f"{n} {c:,}" for n, c in out["rows"].items()))
    print("Revenue by line (GBP, excluding VAT):")
    for bl, basis, total in out["summary"]["revenue"]:
        print(f"  {bl} {basis:9s} {total:>14,.2f}")
    print(f"  Total              {math.fsum(r[2] for r in out['summary']['revenue']):>14,.2f}")
    print(f"Unknown customer: {out['summary']['unknown_rows']:,} rows, revenue £{out['summary']['unknown_revenue']:,.2f}")
    print(f"Exports: {len(out['exported'])} CSV files in {settings['warehouse_dir'] / 'exports'} (ticket_text not exported)")
    a = out["alerts"]
    print(f"Alerts: {a['active']} active ({a['new']} new, {a['cleared']} cleared since the last run), "
          + ", ".join(f"{k} {v}" for k, v in a["by_rule"].items()))
    k = out["kb_index"]
    print(f"KB index: {k['sections']} sections from {k['articles']} current articles ({k['superseded']} superseded left out)")


if __name__ == "__main__":
    main()
