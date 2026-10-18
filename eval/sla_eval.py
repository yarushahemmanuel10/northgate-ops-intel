"""Compare the dashboard's SLA breach rates with the generator's own figures (step 4.6, D-033).

Run:  python -m eval.sla_eval   (after pipeline.warehouse.load)

Evaluation code: it may read the generator and data/clean/.
- Generator: generator.helpdesk.breach_rates on the clean helpdesk file (all closed tickets).
- Warehouse: v_kpi_bq01_summary (closed tickets the SLA can measure; exclusions counted apart).
Every difference is explained ticket by ticket, and the generator's rule is re-run on exactly the
tickets the warehouse measured: if those rates match, the only differences are the exclusions.
"""
from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

import duckdb
import pandas as pd

from generator.config import load_config as generator_config
from generator.helpdesk import breach_rates

ROOT = Path(__file__).resolve().parents[1]


def clean_tickets(path: Path) -> pd.DataFrame:
    with open(path, encoding="utf-8", newline="") as f:
        f.readline()
        return pd.DataFrame(list(csv.DictReader(f)))


def compare(db: Path, clean: pd.DataFrame) -> dict:
    """clean: the clean helpdesk tickets as text columns (as in data/clean/helpdesk_tickets.csv)."""
    cfg = generator_config()
    gen = breach_rates(cfg, clean)
    con = duckdb.connect(str(db), read_only=True)
    try:
        wh = con.execute("SELECT * FROM v_kpi_bq01_summary").df().set_index("priority")
        sla = con.execute("SELECT ticket_id, priority, closed_at, response_excluded_reason, resolution_excluded_reason, "
                          "response_hours > response_target_hours AS resp_breach, "
                          "resolution_hours > resolution_target_hours AS res_breach FROM v_ticket_sla").df()
    finally:
        con.close()
    sla = sla.set_index("ticket_id")
    closed = clean[clean.closed_at.str.strip() != ""]
    out = {"priorities": {}, "reasons_resolution": Counter(), "reasons_response": Counter()}
    for p in ("P1", "P2", "P3", "P4"):
        ids = list(closed.loc[closed.priority == p, "ticket_id"])
        w = sla.loc[ids]
        res_in = w[w.resolution_excluded_reason.isna()].index
        resp_in = w[w.response_excluded_reason.isna()].index
        for r in w.resolution_excluded_reason.dropna():
            out["reasons_resolution"][(p, r.split(":")[0])] += 1
        for r in w.response_excluded_reason.dropna():
            out["reasons_response"][(p, r.split(":")[0].split(" (")[0])] += 1
        # the generator's rule on exactly the tickets the warehouse measured
        sub_res = breach_rates(cfg, closed[closed.ticket_id.isin(res_in)]).loc[p, "resolution_breach"]
        sub_resp = breach_rates(cfg, closed[closed.ticket_id.isin(resp_in)]).loc[p, "response_breach"]
        out["priorities"][p] = {
            "gen_closed": int(gen.loc[p, "closed_tickets"]), "gen_res": float(gen.loc[p, "resolution_breach"]),
            "gen_resp": float(gen.loc[p, "response_breach"]), "target_res": float(gen.loc[p, "target_resolution_breach"]),
            "wh_res_n": int(wh.loc[p, "resolution_measured"]), "wh_res": float(wh.loc[p, "resolution_breached"] / wh.loc[p, "resolution_measured"]),
            "wh_resp_n": int(wh.loc[p, "response_measured"]), "wh_resp": float(wh.loc[p, "response_breached"] / wh.loc[p, "response_measured"]),
            "gen_res_on_wh_tickets": float(sub_res), "gen_resp_on_wh_tickets": float(sub_resp),
            "outcome_differs": int((w.loc[res_in, "res_breach"].astype(bool).values
                                    != (closed.set_index("ticket_id").loc[res_in].pipe(
                                        lambda d: (pd.to_datetime(d.closed_at) - pd.to_datetime(d.opened_at)).dt.total_seconds() / 3600
                                        > cfg["tickets"]["sla_hours"][p]["resolution"]).values)).sum()),
        }
    out["no_valid_priority_in_warehouse"] = int(wh.loc["none", "closed_tickets"]) if "none" in wh.index else 0
    return out


def main() -> None:
    r = compare(ROOT / "data" / "warehouse" / "northgate.duckdb", clean_tickets(ROOT / "data" / "clean" / "helpdesk_tickets.csv"))
    print("Resolution SLA breach rate by priority (closed tickets):")
    print(f"{'':4} {'generator':>16} {'warehouse':>16} {'generator rule on warehouse tickets':>36}")
    for p, x in r["priorities"].items():
        print(f"{p:4} {x['gen_res']:>8.1%} of {x['gen_closed']:<5} {x['wh_res']:>8.1%} of {x['wh_res_n']:<5} "
              f"{x['gen_res_on_wh_tickets']:>20.1%}   (config target {x['target_res']:.0%}; outcomes that differ: {x['outcome_differs']})")
    print("Response SLA breach rate by priority:")
    for p, x in r["priorities"].items():
        print(f"{p:4} {x['gen_resp']:>8.1%} of {x['gen_closed']:<5} {x['wh_resp']:>8.1%} of {x['wh_resp_n']:<5} "
              f"{x['gen_resp_on_wh_tickets']:>20.1%}")
    print("Tickets the warehouse leaves out of resolution SLA, by clean priority and reason:", dict(r["reasons_resolution"]))
    print("Tickets the warehouse leaves out of response SLA, by clean priority and reason:", dict(r["reasons_response"]))
    print(f"Closed tickets with no valid priority in the warehouse (row 'none'): {r['no_valid_priority_in_warehouse']}")


if __name__ == "__main__":
    main()
