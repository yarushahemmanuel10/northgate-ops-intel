"""Reconcile warehouse revenue (v_revenue) with the generator's figures, all five lines.

Run:  python -m eval.reconciliation   (after pipeline.warehouse.load)

Evaluation code: it may use the generator and its answer key; the pipeline may not (D-017).
- BL1, BL3, BL4: the generator's clean modelled revenue: its D-007 revenue check
  (generator.customers.expected_revenue, whole pounds) on the clean customers and contract
  lines, plus the same rule summed without rounding.
- BL2, BL5: the correctly cleaned figures from the generator summary (D-016): £1,072,853 and £734,394.
"""
from __future__ import annotations

import datetime as dt
import math
from pathlib import Path

import duckdb

from generator.config import load_config as generator_config
from generator.customers import _add_months, _month_starts, expected_revenue, generate, make_rngs

ROOT = Path(__file__).resolve().parents[1]
CLEANED = {"BL2": 1072853, "BL5": 734394}       # generator summary, correctly cleaned (D-016)


def generator_modelled() -> dict[str, tuple[float, float]]:
    """{line: (generator's reported whole-pound figure, same rule unrounded)} for BL1, BL3, BL4."""
    cfg = generator_config()
    customers, lines = generate(cfg, *make_rngs(cfg["seed"]))
    reported = expected_revenue(cfg, customers, lines).set_index("business_line")["expected_revenue_window"]
    months = _month_starts(cfg["window"]["start"], cfg["window"]["end"])
    exact = {}
    for bl in ("BL1", "BL3", "BL4"):
        total = 0.0
        for r in lines[lines.business_line == bl].itertuples():
            end = r.end_date if isinstance(r.end_date, dt.date) else None
            n = sum(1 for m in months if r.contract_start <= _add_months(m, 1) - dt.timedelta(days=1)
                    and (end is None or end >= m))
            total += r.annual_value / 12 * n
        exact[bl] = (float(reported[bl]), total)
    return exact


def warehouse_revenue(db: Path) -> dict:
    con = duckdb.connect(str(db), read_only=True)
    try:
        rows = con.execute("SELECT business_line, customer_id, revenue FROM v_revenue").fetchall()
        lines = sorted({r[0] for r in rows})
        by_line = {bl: math.fsum(r[2] for r in rows if r[0] == bl) for bl in lines}             # order-independent
        known = {bl: math.fsum(r[2] for r in rows if r[0] == bl and r[1] != "UNKNOWN") for bl in lines}
        basis = dict(con.execute("SELECT DISTINCT business_line, revenue_basis FROM v_revenue").fetchall())
        excluded = con.execute("SELECT business_line, reason, records FROM v_revenue_excluded").fetchall()
    finally:
        con.close()
    return {"all": by_line, "known": known, "basis": basis, "excluded": excluded}


def _d(x: float) -> float:
    return round(x, 2) + 0.0                     # no "-0.00" from float noise


def main() -> None:
    w = warehouse_revenue(ROOT / "data" / "warehouse" / "northgate.duckdb")
    gen = generator_modelled()
    print(f"{'Line':4} {'Basis':9} {'Warehouse':>14} {'Reference':>14} {'Difference':>11}  Reference source")
    for bl in ("BL1", "BL2", "BL3", "BL4", "BL5"):
        got = w["all"].get(bl, 0.0)
        if bl in gen:
            ref, exact = gen[bl]
            print(f"{bl:4} {w['basis'][bl]:9} {got:>14,.2f} {ref:>14,.2f} {_d(got - ref):>11,.2f}  generator D-007 check (whole £); "
                  f"same rule unrounded £{exact:,.2f}, difference {_d(got - exact):,.2f}")
        else:
            ref = CLEANED[bl]
            print(f"{bl:4} {w['basis'][bl]:9} {got:>14,.2f} {ref:>14,.2f} {_d(got - ref):>11,.2f}  generator summary, correctly cleaned (whole £)")
    print(f"BL5 including Unknown customer £{w['all']['BL5']:,.2f}; excluding £{w['known']['BL5']:,.2f}; "
          f"on Unknown £{w['all']['BL5'] - w['known']['BL5']:,.2f}")
    print(f"BL2 including Unknown £{w['all']['BL2']:,.2f}; excluding £{w['known']['BL2']:,.2f}")
    print("Revenue left out because a value was flagged unusable:",
          w["excluded"] if w["excluded"] else "none (no contract value, start date, bill total, bill month, order total or order date is empty)")


if __name__ == "__main__":
    main()
