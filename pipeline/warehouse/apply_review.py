"""Apply the review decisions and show their effect (step 4.2, D-029).

Run:  python -m pipeline.warehouse.apply_review   (after filling in config/review_decisions.csv)

Builds a baseline warehouse with no decisions in a temporary folder, then rebuilds the real
warehouse (and its exports) with config/review_decisions.csv, and prints: records moved to a
customer, revenue moved off the Unknown customer, and the at-risk list before and after.
It shows the at-risk list only; scoring against the answer key stays in eval/.
"""
from __future__ import annotations

import math
import sys
import hashlib
import tempfile
from pathlib import Path

import duckdb

from pipeline.audit.log import new_session, record
from pipeline.settings import ROOT, load_settings
from pipeline.warehouse import export
from pipeline.warehouse.load import DECISION_COLUMNS, build, load_config, read_decisions


def _facts(db: Path) -> dict:
    con = duckdb.connect(str(db), read_only=True)
    try:
        moved = con.execute("""
            SELECT 'helpdesk' AS source, customer_id, COUNT(*) FROM fact_ticket WHERE match_status = 'human_review' GROUP BY ALL
            UNION ALL SELECT 'shop', customer_id, COUNT(*) FROM fact_order WHERE match_status = 'human_review' GROUP BY ALL
            UNION ALL SELECT 'telecoms', customer_id, COUNT(*) FROM fact_bill WHERE match_status = 'human_review' GROUP BY ALL
            ORDER BY 1, 2""").fetchall()
        unknown_rows = con.execute("""
            SELECT (SELECT COUNT(*) FROM fact_ticket WHERE customer_id = 'UNKNOWN')
                 + (SELECT COUNT(*) FROM fact_order WHERE customer_id = 'UNKNOWN')
                 + (SELECT COUNT(*) FROM fact_bill WHERE customer_id = 'UNKNOWN')""").fetchone()[0]
        rev = con.execute("SELECT business_line, revenue FROM v_revenue WHERE customer_id = 'UNKNOWN'").fetchall()
        unknown_revenue = {bl: math.fsum(v for b, v in rev if b == bl) for bl in sorted({b for b, _ in rev})}
        at_risk = con.execute("SELECT customer_id, company_name, at_risk_score, signals_fired FROM v_at_risk "
                              "WHERE at_risk ORDER BY retention_rank").fetchall()
    finally:
        con.close()
    return {"moved": moved, "unknown_rows": unknown_rows, "unknown_revenue": unknown_revenue, "at_risk": at_risk}


def apply(settings: dict, decisions_path: Path | None = None) -> dict:
    cfg = load_config()
    path = decisions_path or ROOT / cfg["review_decisions"]
    decisions = read_decisions(path)                     # raises ValueError on a bad row
    wd = settings["warehouse_dir"]
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "none.csv").write_text(",".join(DECISION_COLUMNS) + "\n", encoding="utf-8")
        (d / "duplicates.csv").write_bytes((wd / "duplicates.csv").read_bytes())
        build(settings, cfg, warehouse=d, decisions_path=d / "none.csv")
        before = _facts(d / cfg["database"])
    built = build(settings, cfg, decisions_path=path)
    exported = export.run(settings, cfg)
    after = _facts(wd / cfg["database"])
    session, db = new_session(), wd / cfg["database"]            # audit log (D-045): the command line has no role
    record("review_decisions_applied", role="command line", session=session, target=path.name, warehouse_db=db,
           detail={"decision_rows": len(decisions), "records_decided": built["decisions_applied"],
                   "file_sha1": hashlib.sha1(path.read_bytes()).hexdigest(),
                   "unknown_rows_before": before["unknown_rows"], "unknown_rows_after": after["unknown_rows"]})
    record("export_written", role="command line", session=session, target="exports", warehouse_db=db,
           detail={"files": len(exported), "by": "apply_review"})
    return {"decision_rows": len(decisions), "records_decided": built["decisions_applied"],
            "before": before, "after": after}


def main() -> None:
    try:
        out = apply(load_settings())
    except ValueError as exc:
        print(f"Review decisions not applied: {exc}", file=sys.stderr)
        sys.exit(1)
    b, a = out["before"], out["after"]
    print(f"Decisions read: {out['decision_rows']} rows with a decision (blank rows stay in the queue); "
          f"{out['records_decided']} records decided")
    print("Records moved to a customer (source, customer, records):")
    for source, cust, n in a["moved"] or [("-", "-", 0)]:
        print(f"  {source:9s} {cust:9s} {n:>4}")
    print(f"Records on the Unknown customer: {b['unknown_rows']} before, {a['unknown_rows']} after")
    for bl in sorted(set(b["unknown_revenue"]) | set(a["unknown_revenue"])):
        x, y = b["unknown_revenue"].get(bl, 0.0), a["unknown_revenue"].get(bl, 0.0)
        print(f"  {bl} revenue on Unknown: £{x:,.2f} before, £{y:,.2f} after; moved £{x - y:,.2f}")
    for label, rows in (("before", b["at_risk"]), ("after", a["at_risk"])):
        print(f"At-risk customers {label} ({len(rows)}):")
        for cid, name, score, signals in rows:
            print(f"  {cid} {name} - score {score}: {signals}")
    added = sorted({r[0] for r in a["at_risk"]} - {r[0] for r in b["at_risk"]})
    removed = sorted({r[0] for r in b["at_risk"]} - {r[0] for r in a["at_risk"]})
    print(f"Newly flagged: {added or 'none'}; no longer flagged: {removed or 'none'}")
    print("Warehouse and exports rebuilt with these decisions.")


if __name__ == "__main__":
    main()
