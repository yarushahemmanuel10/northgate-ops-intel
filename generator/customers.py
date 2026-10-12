"""Canonical customer universe and CRM contract lines (ground truth).

Run:  python -m generator.customers
Writes data/ground_truth/customers.csv and data/ground_truth/contract_lines.csv and
prints the expected revenue mix against the targets in config/generator.yaml.

Every random choice uses the numpy Generator and Faker instance created by make_rngs()
from the config seed. No global random state is used anywhere.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from faker import Faker

from generator.config import BUSINESS_LINES, load_config
from generator.names import core_name, is_blocked, make_lookalike_pair, make_name

ROOT = Path(__file__).resolve().parents[1]
GROUND_TRUTH_DIR = ROOT / "data" / "ground_truth"
CONTRACT_LINES = ("BL1", "BL2", "BL3", "BL4")
SYNTHETIC_HEADER = "# SYNTHETIC DATA - fictional company Northgate IT Services Ltd; all names and figures invented"


def make_rngs(seed: int) -> tuple[np.random.Generator, Faker]:
    """The only source of randomness: a seeded numpy Generator and a seeded Faker instance."""
    rng = np.random.default_rng(seed)
    fake = Faker("en_GB")
    fake.seed_instance(seed)
    return rng, fake


# --- small date helpers -------------------------------------------------------

def _random_date(lo: dt.date, hi: dt.date, rng: np.random.Generator) -> dt.date:
    return lo + dt.timedelta(days=int(rng.integers(0, (hi - lo).days + 1)))


def _add_months(d: dt.date, months: int) -> dt.date:
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    last_day = (dt.date(year + (month == 12), month % 12 + 1, 1) - dt.timedelta(days=1)).day
    return dt.date(year, month, min(d.day, last_day))


def _next_renewal(start: dt.date, term_years: int, after: dt.date) -> dt.date:
    """First anniversary of `start`, in steps of `term_years`, strictly after `after`."""
    k = 1
    while (r := _add_months(start, 12 * term_years * k)) <= after:
        k += 1
    return r


def _month_starts(start: dt.date, end: dt.date) -> list[dt.date]:
    out, d = [], dt.date(start.year, start.month, 1)
    while d <= end:
        out.append(d)
        d = _add_months(d, 1)
    return out


# --- generation ---------------------------------------------------------------

def _unique_names(cfg: dict[str, Any], rng: np.random.Generator, fake: Faker) -> pd.DataFrame:
    """120 names: look-alike pairs first, then ordinary names, all unique by core name."""
    c, n = cfg["customers"], cfg["volumes"]["customers"]
    blocklist, seen, rows = c["name_blocklist"], set(), []

    def accept(name: str) -> bool:
        key = core_name(name)
        if key in seen or is_blocked(name, blocklist):
            return False
        seen.add(key)
        return True

    group = 0
    while group < c["lookalike_company_pairs"]:
        a, b, sector = make_lookalike_pair(rng)
        if core_name(a) in seen or core_name(b) in seen or is_blocked(a, blocklist) or is_blocked(b, blocklist):
            continue
        accept(a), accept(b)
        group += 1
        rows += [(a, sector, f"LA-{group}"), (b, sector, f"LA-{group}")]

    sectors = list(c["sector_weights"])
    weights = list(c["sector_weights"].values())
    while len(rows) < n:
        sector = str(rng.choice(sectors, p=weights))
        name = make_name(sector, rng, fake)
        if accept(name):
            rows.append((name, sector, ""))

    df = pd.DataFrame(rows, columns=["company_name", "sector", "lookalike_group"])
    return df.iloc[rng.permutation(n)].reset_index(drop=True)


def generate(cfg: dict[str, Any], rng: np.random.Generator, fake: Faker) -> tuple[pd.DataFrame, pd.DataFrame]:
    v, c, cv = cfg["volumes"], cfg["customers"], cfg["contract_values"]
    as_of, window_start = cfg["as_of_date"], cfg["window"]["start"]
    trend_start = dt.date(2026, 4, 1)   # start of the "previous 3 months" window (D-002)
    n = v["customers"]

    customers = _unique_names(cfg, rng, fake)
    customers.insert(0, "account_id", [f"ACC-{i:04d}" for i in range(1, n + 1)])
    managers = c["account_managers"]
    customers["account_manager"] = [managers[int(i)] for i in rng.integers(len(managers), size=n)]

    # Disjoint roles: new, closed, at-risk; everyone else is an ordinary established customer.
    order = rng.permutation(n)
    n_new, n_closed, n_risk = v["new_customers_in_window"], v["closed_customers"], cfg["at_risk_customers"]
    role = np.array(["established"] * n, dtype=object)
    role[order[:n_new]] = "new"
    role[order[n_new:n_new + n_closed]] = "closed"
    role[order[n_new + n_closed:n_new + n_closed + n_risk]] = "at_risk"

    first_start, closed_date = [], []
    for r in role:
        lo, hi = c["new_start_range"] if r == "new" else c["established_start_range"]
        first_start.append(_random_date(lo, hi, rng))
        closed_date.append(_random_date(*c["closed_date_range"], rng) if r == "closed" else None)

    uptake = cfg["business_line_uptake"]
    # Exactly round(rate * n) customers per line, so uptake matches the config rather than
    # varying with the seed (D-007).
    takes = {}
    for bl in BUSINESS_LINES:
        flags = np.zeros(n, dtype=bool)
        flags[rng.choice(n, size=round(uptake[bl] * n), replace=False)] = True
        takes[bl] = flags
    for i in range(n):
        if role[i] == "at_risk":          # needs tickets (BL1) and spend to fall (BL2 or BL5)
            takes["BL1"][i] = True
            if not (takes["BL2"][i] or takes["BL5"][i]):
                takes["BL5"][i] = True
        if not any(takes[bl][i] for bl in BUSINESS_LINES):
            takes["BL1"][i] = True

    lo_b, hi_b = cv["shop_annual_budget_range"]
    budgets = [int(round(rng.uniform(lo_b, hi_b), -2)) if takes["BL5"][i] else 0 for i in range(n)]

    customers["first_contract_start"] = first_start
    customers["status"] = ["closed" if r == "closed" else "active" for r in role]
    customers["closed_date"] = closed_date
    customers["is_new_in_window"] = role == "new"
    customers["at_risk"] = role == "at_risk"
    customers["business_lines"] = [";".join(bl for bl in BUSINESS_LINES if takes[bl][i]) for i in range(n)]
    customers["shop_annual_budget"] = budgets

    terms = list(cv["term_years_weights"])
    term_p = list(cv["term_years_weights"].values())
    off_lo, off_hi = c["additional_line_offset_months"]
    lines = []
    for i in range(n):
        taken = [bl for bl in CONTRACT_LINES if takes[bl][i]]
        end = closed_date[i]
        latest_start = (end or as_of) - dt.timedelta(days=30)
        for k, bl in enumerate(taken):
            start = first_start[i]
            if k > 0:
                start = _add_months(first_start[i], int(rng.integers(off_lo, off_hi + 1)))
                if start > latest_start:
                    start = first_start[i]
            term = int(rng.choice(terms, p=term_p))
            lo_v, hi_v = cv["annual_value_range"][bl]
            lines.append({
                "account_id": customers.at[i, "account_id"],
                "business_line": bl,
                "annual_value": int(round(rng.uniform(lo_v, hi_v), -2)),
                "contract_start": start,
                "term_years": term,
                "renewal_date": None if end else _next_renewal(start, term, as_of),
                "end_date": end,
                "revenue_source": "billing" if bl == "BL2" else "crm_modelled",
            })
    lines_df = pd.DataFrame(lines)
    lines_df.insert(0, "line_id", [f"CL-{i:04d}" for i in range(1, len(lines_df) + 1)])

    # Sanity: the role rules the rest of the pipeline relies on.
    assert all(customers.loc[customers.at_risk, "first_contract_start"] < trend_start)
    assert customers.loc[customers.is_new_in_window, "first_contract_start"].min() >= window_start
    return customers, lines_df


# --- expected revenue mix ------------------------------------------------------

def expected_revenue(cfg: dict[str, Any], customers: pd.DataFrame, lines: pd.DataFrame) -> pd.DataFrame:
    """Expected revenue per business line over the data window, in whole months.

    BL1/BL3/BL4: CRM annual value / 12 per active month (D-003, modelled).
    BL2: CRM annual value / 12 per active month, as a stand-in for the bills the telecoms
         generator will produce around that value. Real BL2 revenue comes from billing.
    BL5: shop annual budget / 12 per month the customer is active, as a stand-in for orders.
    A month counts as active if the contract overlaps any part of it.
    """
    months = _month_starts(cfg["window"]["start"], cfg["window"]["end"])

    def active_months(start: dt.date, end: dt.date | None) -> int:
        return sum(1 for m in months
                   if start <= _add_months(m, 1) - dt.timedelta(days=1) and (end is None or end >= m))

    totals = dict.fromkeys(BUSINESS_LINES, 0.0)
    for row in lines.itertuples():
        totals[row.business_line] += row.annual_value / 12 * active_months(row.contract_start, row.end_date)
    for row in customers.itertuples():
        if row.shop_annual_budget:
            totals["BL5"] += row.shop_annual_budget / 12 * active_months(row.first_contract_start, row.closed_date)

    grand = sum(totals.values())
    out = pd.DataFrame({
        "business_line": list(totals),
        "expected_revenue_window": [round(t) for t in totals.values()],
        "share": [t / grand for t in totals.values()],
        "target": [cfg["revenue_share_targets"][bl] for bl in totals],
    })
    out["diff_pp"] = (out["share"] - out["target"]) * 100
    return out


# --- output --------------------------------------------------------------------

def _write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(SYNTHETIC_HEADER + "\n")
        df.to_csv(f, index=False, lineterminator="\n")


def read_ground_truth_csv(path: Path) -> pd.DataFrame:
    """Read a ground-truth CSV written by this module (skips the synthetic-data header line)."""
    return pd.read_csv(path, skiprows=1, keep_default_na=False)


def write_ground_truth(cfg: dict[str, Any], out_dir: Path = GROUND_TRUTH_DIR,
                       seed: int | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng, fake = make_rngs(cfg["seed"] if seed is None else seed)
    customers, lines = generate(cfg, rng, fake)
    _write_csv(customers, out_dir / "customers.csv")
    _write_csv(lines, out_dir / "contract_lines.csv")
    return customers, lines


def main() -> None:
    cfg = load_config()
    customers, lines = write_ground_truth(cfg)
    as_of = cfg["as_of_date"]
    renew_90 = lines["renewal_date"].map(lambda d: d is not None and as_of < d <= as_of + dt.timedelta(days=90))
    print(f"Wrote {len(customers)} customers and {len(lines)} contract lines to {GROUND_TRUTH_DIR}")
    print(f"Roles: new={int(customers.is_new_in_window.sum())}, closed={int((customers.status == 'closed').sum())}, "
          f"at_risk={int(customers.at_risk.sum())}, "
          f"lookalike groups={customers.loc[customers.lookalike_group != '', 'lookalike_group'].nunique()}")
    print(f"Contract lines renewing 1 Oct - 29 Dec 2026: {int(renew_90.sum())}")
    rev = expected_revenue(cfg, customers, lines)
    print("\nExpected revenue mix over the 18-month window (see expected_revenue() docstring):")
    print(rev.to_string(index=False, formatters={"share": "{:.1%}".format, "target": "{:.0%}".format,
                                                 "diff_pp": "{:+.1f}".format}))
    total = rev["expected_revenue_window"].sum()
    print(f"\nTotal over window: £{total:,.0f}; annualised (÷1.5): £{total / 1.5:,.0f} (fact sheet: about £3.2m)")


if __name__ == "__main__":
    main()
