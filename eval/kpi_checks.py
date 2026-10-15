"""Explain two KPI figures against the answer key (step 4.5, D-032).

Run:  python -m eval.kpi_checks   (after pipeline.warehouse.load)

Evaluation code: it may read data/ground_truth/.
- BQ-05: renewing lines in the warehouse against the clean contract lines.
- BQ-12: tickets still linking the superseded article against the links that were planted.
"""
from __future__ import annotations

import csv
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]


def _truth(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        f.readline()
        return list(csv.DictReader(f))


def renewals_check(db: Path, truth: Path) -> dict:
    clean = {r["line_id"] for r in _truth(truth / "contract_lines.csv")
             if not r["end_date"] and "2026-10-01" <= r["renewal_date"] <= "2026-12-29"}
    con = duckdb.connect(str(db), read_only=True)
    try:
        rows = {r[0]: r[1] for r in con.execute("SELECT line_id, renewal_status FROM v_kpi_bq05_renewals").fetchall()}
    finally:
        con.close()
    shown = {k for k, v in rows.items() if v == "renews in next 90 days"}
    removed = {e["record_key"] for e in _truth(truth / "known_issues.csv")
               if e["qi"] == "QI-08" and e["field"] == "renewal_date"}
    missing_from_view = clean - shown
    return {"clean": len(clean), "warehouse": len(shown), "only_clean": sorted(missing_from_view),
            "only_warehouse": sorted(shown - clean),
            "explained_by_qi08": sorted(missing_from_view & removed),
            "listed_as_missing_date": sorted(k for k in missing_from_view if rows.get(k) == "renewal date missing")}


def superseded_links_check(db: Path, truth: Path) -> dict:
    tickets = {r["ticket_id"]: r for r in _truth(truth / "tickets.csv")}
    planted = {k for k, r in tickets.items() if r["kb_link_outdated"] == "True"}
    removed = {e["record_key"] for e in _truth(truth / "known_issues.csv")
               if e["qi"] == "QI-08" and e["field"] == "kb_article_id"}
    con = duckdb.connect(str(db), read_only=True)
    try:
        shown = con.execute("SELECT SUM(linked_after_superseded) FROM v_kpi_bq12_kb_usage").fetchone()[0]
        linked = {r[0] for r in con.execute(
            "SELECT t.ticket_id FROM fact_ticket t JOIN dim_kb_article a ON t.kb_article_id = a.kb_id "
            "WHERE a.superseded_on IS NOT NULL AND t.opened_date >= a.superseded_on").fetchall()}
    finally:
        con.close()
    return {"planted": len(planted), "warehouse": int(shown), "removed_by_qi08": len(planted & removed),
            "planted_minus_removed": len(planted - removed), "same_tickets": linked == planted - removed}


def main() -> None:
    db, truth = ROOT / "data" / "warehouse" / "northgate.duckdb", ROOT / "data" / "ground_truth"
    r = renewals_check(db, truth)
    print(f"BQ-05 renewing lines: clean data {r['clean']}, warehouse {r['warehouse']}. "
          f"Only in clean: {r['only_clean']} (renewal date removed by QI-08: {r['explained_by_qi08']}; "
          f"listed under 'renewal date missing': {r['listed_as_missing_date']}). Only in warehouse: {r['only_warehouse'] or 'none'}.")
    s = superseded_links_check(db, truth)
    print(f"BQ-12 links to superseded articles: planted {s['planted']}, warehouse {s['warehouse']}; "
          f"links removed by QI-08 {s['removed_by_qi08']}; planted minus removed {s['planted_minus_removed']}; "
          f"same tickets: {s['same_tickets']}.")


if __name__ == "__main__":
    main()
