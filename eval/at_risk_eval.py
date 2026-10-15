"""Score the at-risk view (v_at_risk, D-028) against the answer key.

Run:  python -m eval.at_risk_eval   (after pipeline.warehouse.load)

Evaluation code: it may read data/ground_truth/. Two views:
- before review decisions: the warehouse as built (config/review_decisions.csv is empty);
- after: every queued record decided correctly from the answer key, written to a temporary
  decisions file and a temporary warehouse. The committed decisions file is never touched.
"""
from __future__ import annotations

import csv
import tempfile
from pathlib import Path

import duckdb

from eval.resolution_eval import load_crosswalk
from pipeline.settings import load_settings
from pipeline.warehouse.load import DECISION_COLUMNS, build, load_config

ROOT = Path(__file__).resolve().parents[1]


def _read(path: Path, skip_marker: bool = False) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        if skip_marker:
            f.readline()
        return list(csv.DictReader(f))


def truth_at_risk(truth: Path) -> set[str]:
    return {r["account_id"] for r in _read(truth / "customers.csv", True) if r["at_risk"] == "True"}


def flagged(db: Path) -> list[tuple]:
    con = duckdb.connect(str(db), read_only=True)
    try:
        return con.execute("SELECT customer_id, signals_fired, at_risk_score FROM v_at_risk WHERE at_risk "
                           "ORDER BY retention_rank").fetchall()
    finally:
        con.close()


def correct_decisions(resolved: Path, validated: Path, truth: Path) -> list[dict]:
    rows = load_crosswalk(resolved, validated, truth)
    system = {"helpdesk_tickets": "helpdesk", "shop_orders": "shop", "telecoms_bills": "telecoms",
              "telecoms_contracts": "telecoms"}
    return [{"source_system": system[r["source_table"]], "source_key": r["source_key"], "decided_account_id": r["truth"],
             "reviewer": "answer key (simulated)", "decided_on": "2026-10-15", "reason": "evaluation only"}
            for r in rows if r["status"] == "review"]


def score(found: list[tuple], risk: set[str]) -> dict:
    ids = {f[0] for f in found}
    return {"found": sorted(ids & risk), "missed": sorted(risk - ids), "healthy_flagged": sorted(ids - risk)}


def main() -> None:
    s = load_settings()
    cfg = load_config()
    truth = ROOT / "data" / "ground_truth"
    risk = truth_at_risk(truth)
    before = flagged(s["warehouse_dir"] / cfg["database"])
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        decisions = correct_decisions(s["resolved_dir"], s["validated_dir"], truth)
        with open(d / "decisions.csv", "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=DECISION_COLUMNS, lineterminator="\n")
            w.writeheader()
            w.writerows(decisions)
        (d / "duplicates.csv").write_bytes((s["warehouse_dir"] / "duplicates.csv").read_bytes())
        out = build(s, cfg, warehouse=d, decisions_path=d / "decisions.csv")
        after = flagged(d / cfg["database"])
    for label, rows, extra in (("Before review decisions", before, ""),
                               ("After every queued record is decided correctly", after,
                                f" ({out['decisions_applied']} records decided)")):
        sc = score(rows, risk)
        print(f"{label}{extra}: {len(sc['found'])} of {len(risk)} at-risk customers found; "
              f"healthy wrongly flagged {sc['healthy_flagged']}; missed {sc['missed']}")
        for cid, signals, n in rows:
            print(f"    {cid} {'AT RISK' if cid in risk else 'healthy'}  score {n}: {signals}")


if __name__ == "__main__":
    main()
