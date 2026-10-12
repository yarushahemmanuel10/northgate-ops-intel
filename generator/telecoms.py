"""Telecoms contracts and monthly bills (clean) and their ground truth.

Run:  python -m generator.telecoms
Writes data/raw/telecoms_contracts.csv, data/raw/telecoms_bills.csv and
data/ground_truth/telecoms_accounts.csv, and prints realised BL2 revenue, the at-risk
spend fall and the at-risk coverage list.

Rules (D-003, D-012):
- Exactly one telecoms account and contract per CRM BL2 contract line (clean one-to-one).
  Contract end date = CRM end date (closed customers) or current renewal date.
- Amounts exclude VAT. Monthly bill = fixed line rental + call charges (never negative).
  Twelve months of bills come to roughly the CRM BL2 annual value.
- The bill for month M is dated the last day of M. The September 2026 bill is therefore
  issued on 30 September and exists at the as-of date.
- No bill before the contract starts, after it ends, or outside the data window. Partial
  first and last months are prorated by days.
- Account names are written in the billing system's style (upper case, no legal suffix,
  20 characters). Deliberate mess (QI-03, QI-04, QI-10, QI-11) is added in a later step.
- The raw files never contain ACC- IDs (QI-02).
"""
from __future__ import annotations

import calendar
import datetime as dt
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from generator.customers import GROUND_TRUTH_DIR, _write_csv, expected_revenue, generate, make_rngs
from generator.config import load_config
from generator.helpdesk import LAST_Q, PREV_Q
from generator.names import name_variant
from generator.streams import named_stream

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
CONTRACT_COLUMNS = ["contract_ref", "account_no", "account_name", "service_type", "monthly_charge",
                    "start_date", "end_date"]
BILL_COLUMNS = ["bill_id", "account_no", "bill_month", "bill_date", "line_rental", "call_charges", "total"]


def billing_style(name: str) -> str:
    """How the billing system writes every account name (deterministic, no randomness)."""
    return name_variant(name, "uppercase_truncate", rng=None)


def _month_end(d: dt.date) -> dt.date:
    return dt.date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def _months(lo: dt.date, hi: dt.date) -> list[dt.date]:
    out, d = [], dt.date(lo.year, lo.month, 1)
    while d <= hi:
        out.append(d)
        d = dt.date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return out


def generate_telecoms(cfg: dict[str, Any], customers: pd.DataFrame, lines: pd.DataFrame,
                      seed: int | None = None) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    tel = cfg["telecoms"]
    root = named_stream(cfg["seed"] if seed is None else seed, "telecoms")
    accounts_rng, calls_rng, risk_rng = root.spawn(3)
    names = customers.set_index("account_id").company_name
    at_risk = set(customers.loc[customers.at_risk, "account_id"])

    bl2 = lines[lines.business_line == "BL2"].reset_index(drop=True)
    order = accounts_rng.permutation(len(bl2))          # account numbers not in ACC- order
    first_no = tel["first_account_number"]
    services = tel["service_types"]
    contracts, truth = [], []
    for k, i in enumerate(order):
        line = bl2.iloc[int(i)]
        account_no = f"TEL-{first_no + k}"
        rental = round(line.annual_value * (1 - tel["call_charge_share"]) / 12, 2)
        contracts.append({
            "contract_ref": f"TC-{k + 1:05d}",
            "account_no": account_no,
            "account_name": billing_style(names[line.account_id]),
            "service_type": services[int(accounts_rng.integers(len(services)))],
            "monthly_charge": rental,
            "start_date": line.contract_start,
            "end_date": line.end_date if line.end_date is not None else line.renewal_date,
            "x_account_id": line.account_id, "x_annual": line.annual_value,
            "x_billing_end": line.end_date,          # None = still active at the as-of date
        })
        truth.append({"account_no": account_no, "account_id": line.account_id, "line_id": line.line_id})
    contracts_df = pd.DataFrame(contracts).sort_values("contract_ref").reset_index(drop=True)

    # --- monthly bills ---------------------------------------------------------------
    w_start, w_end = cfg["window"]["start"], cfg["window"]["end"]
    shape = tel["call_charge_shape"]
    lo_r, hi_r = tel["at_risk_fall"]["call_reduction_range"]
    bills = []
    for c in contracts_df.itertuples():
        first = max(c.start_date, w_start)
        last = min(c.x_billing_end - dt.timedelta(days=1), w_end) if c.x_billing_end else w_end
        if last < first:
            continue
        mean_calls = c.x_annual * tel["call_charge_share"] / 12
        reduction = risk_rng.uniform(lo_r, hi_r) if c.x_account_id in at_risk else 0.0
        for m in _months(first, last):
            m_end = _month_end(m)
            active = (min(m_end, last) - max(m, first)).days + 1
            frac = active / calendar.monthrange(m.year, m.month)[1]
            calls = calls_rng.gamma(shape, mean_calls / shape) * frac
            if m >= LAST_Q[0]:
                calls *= 1 - reduction
            bills.append({"account_no": c.account_no, "bill_month": m.strftime("%Y-%m"),
                          "bill_date": m_end, "line_rental": round(c.monthly_charge * frac, 2),
                          "call_charges": round(calls, 2), "x_account_id": c.x_account_id})
    bills_df = pd.DataFrame(bills).sort_values(["bill_month", "account_no"]).reset_index(drop=True)

    # Guarantee the at-risk rule: if random call variation left a fall smaller than the
    # rule, scale that customer's Jul-Sep call charges down until it holds.
    max_ratio = tel["at_risk_fall"]["max_ratio"]
    rescaled = []
    for acc in sorted(at_risk & set(bills_df.x_account_id)):
        rows = bills_df[bills_df.x_account_id == acc]
        prev = rows[rows.bill_month.between("2026-04", "2026-06")]
        last = rows[rows.bill_month.between("2026-07", "2026-09")]
        p_tot = (prev.line_rental + prev.call_charges).sum()
        l_rent, l_calls = last.line_rental.sum(), last.call_charges.sum()
        if l_rent + l_calls > max_ratio * p_tot:
            target_calls = max(0.0, max_ratio * p_tot - l_rent) * 0.99
            scale = target_calls / l_calls if l_calls else 0.0
            bills_df.loc[last.index, "call_charges"] = (last.call_charges * scale).round(2)
            rescaled.append(acc)
    bills_df.attrs["rescaled_at_risk"] = rescaled

    bills_df["total"] = (bills_df.line_rental + bills_df.call_charges).round(2)
    bills_df.insert(0, "bill_id", [f"BILL-{i:06d}" for i in range(1, len(bills_df) + 1)])

    raw_contracts = contracts_df[CONTRACT_COLUMNS].copy()
    raw_bills = bills_df[BILL_COLUMNS].copy()
    truth_df = pd.DataFrame(truth).sort_values("account_no").reset_index(drop=True)
    return raw_contracts, raw_bills, truth_df


# --- measurements -----------------------------------------------------------------------

def quarter_spend(bills: pd.DataFrame, truth: pd.DataFrame) -> pd.DataFrame:
    df = bills.merge(truth[["account_no", "account_id"]], on="account_no")
    month = df.bill_month.astype(str)
    prev = df[month.between("2026-04", "2026-06")].groupby("account_id").total.sum()
    last = df[month.between("2026-07", "2026-09")].groupby("account_id").total.sum()
    out = pd.DataFrame({"prev": prev, "last": last}).fillna(0.0)
    out["ratio"] = out["last"] / out["prev"]
    return out


def meets_fall(spend: pd.DataFrame, cfg) -> pd.Series:
    return (spend["prev"] > 0) & (spend["ratio"] <= cfg["telecoms"]["at_risk_fall"]["max_ratio"])


def at_risk_coverage(customers: pd.DataFrame) -> pd.DataFrame:
    """Which source(s) each at-risk customer's spend fall comes from."""
    rows = []
    for c in customers[customers.at_risk].itertuples():
        bls = c.business_lines.split(";")
        sources = [s for s in ("BL2", "BL5") if s in bls]
        rows.append({"account_id": c.account_id, "company_name": c.company_name,
                     "fall_sources": "+".join(sources) or "NONE"})
    return pd.DataFrame(rows)


def write(raw_contracts, raw_bills, truth, raw_dir: Path = RAW_DIR, truth_dir: Path = GROUND_TRUTH_DIR):
    _write_csv(raw_contracts.assign(
        start_date=raw_contracts.start_date.map(lambda d: d.isoformat()),
        end_date=raw_contracts.end_date.map(lambda d: "" if d is None else d.isoformat())),
        raw_dir / "telecoms_contracts.csv")
    _write_csv(raw_bills.assign(bill_date=raw_bills.bill_date.map(lambda d: d.isoformat())),
               raw_dir / "telecoms_bills.csv")
    _write_csv(truth, truth_dir / "telecoms_accounts.csv")


def build(cfg, raw_dir: Path = RAW_DIR, truth_dir: Path = GROUND_TRUTH_DIR, seed: int | None = None):
    customers, lines = generate(cfg, *make_rngs(cfg["seed"] if seed is None else seed))
    raw_contracts, raw_bills, truth = generate_telecoms(cfg, customers, lines, seed=seed)
    write(raw_contracts, raw_bills, truth, raw_dir, truth_dir)
    return customers, lines, raw_contracts, raw_bills, truth


def main() -> None:
    cfg = load_config()
    customers, lines, contracts, bills, truth = build(cfg)
    print(f"Wrote {len(contracts)} telecoms contracts and {len(bills)} bills "
          f"({bills.bill_month.min()} to {bills.bill_month.max()})")
    collisions = contracts.account_name.duplicated(keep=False).sum()
    print(f"Account names identical after billing-system style: {int(collisions)}")

    realised = bills.total.sum()
    exp = expected_revenue(cfg, customers, lines).set_index("business_line")
    stand_in = exp.loc["BL2", "expected_revenue_window"]
    print(f"\nBL2 revenue over the window: realised £{realised:,.0f} vs D-007 stand-in £{stand_in:,.0f} "
          f"({(realised / stand_in - 1):+.1%})")
    mix = exp.expected_revenue_window.astype(float).copy()
    mix["BL2"] = realised
    share = mix / mix.sum()
    target = pd.Series(cfg["revenue_share_targets"])
    print("Revenue mix with realised BL2 (BL5 still the shop-budget stand-in):")
    print(pd.DataFrame({"share": share.map("{:.1%}".format), "target": target.map("{:.0%}".format),
                        "diff_pp": ((share - target) * 100).map("{:+.1f}".format)}).to_string())

    spend = quarter_spend(bills, truth)
    flags = meets_fall(spend, cfg)
    r = cfg["telecoms"]["at_risk_fall"]["max_ratio"]
    risk = customers.loc[customers.at_risk, "account_id"]
    risk_bl2 = [a for a in risk if a in spend.index]
    print(f"\nAt-risk BL2 spend fall rule: Jul-Sep bills <= {r} x Apr-Jun")
    print(spend.loc[risk_bl2].assign(meets=flags.loc[risk_bl2]).round(2).to_string())
    print(f"At-risk customers whose Jul-Sep calls were scaled down further to meet the rule: "
          f"{bills.attrs.get('rescaled_at_risk', [])}")
    active = set(customers.loc[customers.status == "active", "account_id"])
    healthy = [a for a in spend.index if a in active and a not in set(risk)]
    chance = flags.loc[healthy]
    print(f"Active BL2 customers not at risk that meet the rule by chance: {int(chance.sum())} of {len(healthy)} "
          f"{list(chance[chance].index)}")
    closed_hit = [a for a in spend.index if a not in active and flags.get(a, False)]
    print(f"(Closed customers are outside BQ-06, which covers active customers; their bills stop at "
          f"closure, so these meet the rule mechanically: {closed_hit})")

    print("\nAt-risk coverage (sources of the spend fall):")
    cov = at_risk_coverage(customers)
    cov["BL2 fall in place"] = cov.account_id.map(lambda a: bool(flags.get(a, False)))
    cov["needs BL5 fall from step 1.7"] = ~cov["BL2 fall in place"]
    print(cov.to_string(index=False))


if __name__ == "__main__":
    main()
