"""Online shop orders (clean) and their ground truth.

Run:  python -m generator.shop
Writes data/raw/shop_orders.json (served by shop_api/app.py; the platform must read it
through the API) and data/ground_truth/shop_customers.csv, and prints realised BL5
revenue, the final revenue mix, the at-risk shop spend fall and the coverage table.

Rules (D-012, D-013):
- Only customers who use the shop (BL5) order, from `customer_since` (or the window start)
  to the day before closing (or the as-of date). Amounts exclude VAT.
- Some customers have a second shop account. One healthy customer moves most ordering to
  their second account from July 2026 (a false-alarm trap if accounts are not matched).
- At-risk customers place fewer orders from July 2026; their fall is measured on combined
  spend across all their shop accounts.
- Every order is clean: no cancellations, refunds or duplicates yet (QI-04, QI-10 later).
- The raw file never contains ACC- IDs (QI-02) and carries "synthetic": true.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from generator.config import BUSINESS_LINES, load_config
from generator.customers import GROUND_TRUTH_DIR, _write_csv, expected_revenue, generate, make_rngs
from generator.helpdesk import LAST_Q, PREV_Q, _open_time
from generator.streams import named_stream

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "clean"   # clean output; only generator.messiness writes data/raw (D-015)
ORDERS_FILE = "shop_orders.json"
TIME_FMT = "%Y-%m-%d %H:%M"
SYNTHETIC_NOTE = ("SYNTHETIC DATA - fictional company Northgate IT Services Ltd; all names and "
                  "figures invented. Amounts exclude VAT.")


def expected_order_value(cfg) -> float:
    shop = cfg["shop"]
    cat = shop["catalogue"]
    w = sum(p["weight"] for p in cat)
    per_line = sum(p["weight"] / w * p["unit_price"] * (1 + p["max_qty"]) / 2 for p in cat)
    return per_line * sum(shop["lines_per_order"]) / 2


def _days(lo: dt.date, hi: dt.date) -> list[dt.date]:
    return [lo + dt.timedelta(days=i) for i in range((hi - lo).days + 1)] if hi >= lo else []


def generate_shop(cfg: dict[str, Any], customers: pd.DataFrame, seed: int | None = None
                  ) -> tuple[dict, pd.DataFrame]:
    shop = cfg["shop"]
    root = named_stream(cfg["seed"] if seed is None else seed, "shop")
    accounts_rng, orders_rng, times_rng, contents_rng, delivery_rng, risk_rng = root.spawn(6)
    as_of, w_start = cfg["as_of_date"], cfg["window"]["start"]

    bl5 = customers[customers.business_lines.str.split(";").map(lambda b: "BL5" in b)].reset_index(drop=True)
    at_risk = set(customers.loc[customers.at_risk, "account_id"])

    # --- who gets a second account, who switches ----------------------------------------
    eligible = bl5[(bl5.status == "active") & ~bl5.account_id.isin(at_risk) & (bl5.customer_since < w_start)]
    big = eligible[eligible.shop_annual_budget >= eligible.shop_annual_budget.median()].account_id.tolist()
    switchers = [str(x) for x in accounts_rng.choice(sorted(big), size=shop["account_switchers"], replace=False)]
    risk_with_second = [str(accounts_rng.choice(sorted(at_risk & set(bl5.account_id))))]
    others = sorted(set(bl5.loc[bl5.status == "active", "account_id"]) - at_risk - set(switchers))
    n_rest = shop["second_accounts"] - len(switchers) - len(risk_with_second)
    rest = [str(x) for x in accounts_rng.choice(others, size=n_rest, replace=False)]
    with_second = set(switchers) | set(risk_with_second) | set(rest)

    # shop customer IDs, numbered in shuffled order
    accounts = [(acc, "primary") for acc in bl5.account_id] + [(acc, "second") for acc in sorted(with_second)]
    order_ids = accounts_rng.permutation(len(accounts))
    shop_ids = {}
    truth_rows = []
    for k, i in enumerate(order_ids):
        acc, kind = accounts[int(i)]
        sid = shop["first_customer_id"] + k
        shop_ids[(acc, kind)] = sid
        truth_rows.append({"shop_customer_id": sid, "account_id": acc, "account_kind": kind,
                           "switches_to_second": acc in switchers})
    truth = pd.DataFrame(truth_rows).sort_values("shop_customer_id").reset_index(drop=True)

    # --- orders ----------------------------------------------------------------------------
    names = customers.set_index("account_id").company_name
    cat = shop["catalogue"]
    weights = np.array([p["weight"] for p in cat], dtype=float)
    weights /= weights.sum()
    e_order = expected_order_value(cfg)
    lo_red, hi_red = shop["at_risk_fall"]["order_reduction_range"]
    orders = []
    for c in bl5.itertuples():
        first = max(w_start, c.customer_since)
        last = as_of if c.closed_date is None else c.closed_date - dt.timedelta(days=1)
        days = _days(first, last)
        if not days:
            continue
        n = int(orders_rng.poisson(c.shop_annual_budget / 365 * len(days) / e_order))
        reduction = risk_rng.uniform(lo_red, hi_red) if c.account_id in at_risk else 0.0
        for _ in range(n):
            placed = _open_time(days, cfg, times_rng)
            in_last_q = placed.date() >= LAST_Q[0]
            if reduction and in_last_q and risk_rng.random() < reduction:
                continue                                    # at-risk: fewer orders from July
            kind = "primary"
            if c.account_id in with_second:
                share = shop["switch_share_after"] if (c.account_id in switchers and in_last_q) \
                    else shop["second_account_share"]
                if accounts_rng.random() < share:
                    kind = "second"
            n_lines = int(contents_rng.integers(shop["lines_per_order"][0], shop["lines_per_order"][1] + 1))
            picks = contents_rng.choice(len(cat), size=n_lines, replace=False, p=weights)
            lines = []
            for j in sorted(picks):
                p = cat[int(j)]
                q = int(contents_rng.integers(1, p["max_qty"] + 1))
                lines.append({"sku": p["sku"], "product": p["name"], "quantity": q,
                              "unit_price": round(p["unit_price"], 2),
                              "line_total": round(p["unit_price"] * q, 2)})
            promised = placed.date() + dt.timedelta(days=int(delivery_rng.integers(
                shop["promised_days"][0], shop["promised_days"][1] + 1)))
            delivered = placed.date() + dt.timedelta(days=int(delivery_rng.integers(
                shop["delivery_days"][0], shop["delivery_days"][1] + 1)))
            orders.append({"account_id": c.account_id, "customer_id": shop_ids[(c.account_id, kind)],
                           "company_name": names[c.account_id], "placed": placed,
                           "promised": promised, "delivered": delivered, "lines": lines,
                           "order_total": round(sum(x["line_total"] for x in lines), 2)})

    df = pd.DataFrame(orders).sort_values(["placed", "customer_id"], kind="stable").reset_index(drop=True)

    # Guarantee the at-risk rule on combined spend: if order thinning left too small a fall,
    # drop that customer's largest Jul-Sep orders until it holds.
    max_ratio = shop["at_risk_fall"]["max_ratio"]
    adjusted = []
    for acc in sorted(at_risk & set(df.account_id)):
        d = df.placed.dt.date
        prev = df[(df.account_id == acc) & (d >= PREV_Q[0]) & (d <= PREV_Q[1])].order_total.sum()
        last_rows = df[(df.account_id == acc) & (d >= LAST_Q[0]) & (d <= LAST_Q[1])]
        drop = []
        total = last_rows.order_total.sum()
        for idx in last_rows.sort_values("order_total", ascending=False).index:
            if total <= max_ratio * prev:
                break
            drop.append(idx)
            total -= df.at[idx, "order_total"]
        if drop:
            adjusted.append(acc)
            df = df.drop(index=drop)
    df = df.reset_index(drop=True)
    df.insert(0, "order_id", [f"SO-{100001 + i}" for i in range(len(df))])

    moment = dt.datetime.combine(as_of + dt.timedelta(days=1), dt.time())
    records = []
    for r in df.itertuples():
        delivered = r.delivered if r.delivered <= as_of else None
        if delivered is not None:
            status = "delivered"
        elif r.placed <= moment - dt.timedelta(days=1):
            status = "dispatched"
        else:
            status = "placed"
        records.append({"order_id": r.order_id, "customer_id": int(r.customer_id),
                        "company_name": r.company_name, "order_date": r.placed.strftime(TIME_FMT),
                        "status": status, "promised_date": r.promised.isoformat(),
                        "delivered_date": delivered.isoformat() if delivered else None,
                        "lines": r.lines, "order_total": r.order_total})
    payload = {"synthetic": True, "note": SYNTHETIC_NOTE, "currency": "GBP", "vat": "excluded",
               "orders": records}
    truth.attrs["adjusted_at_risk"] = adjusted
    truth.attrs["switchers"] = switchers
    truth.attrs["risk_with_second"] = risk_with_second
    return payload, truth


# --- measurements -------------------------------------------------------------------------

def orders_frame(payload: dict) -> pd.DataFrame:
    df = pd.DataFrame(payload["orders"])
    df["order_dt"] = pd.to_datetime(df.order_date, format=TIME_FMT)
    return df


def quarter_shop_spend(payload: dict, truth: pd.DataFrame, by: str) -> pd.DataFrame:
    """Apr-Jun vs Jul-Sep 2026 spend, per shop account (by='customer_id') or per customer (by='account_id')."""
    df = orders_frame(payload).merge(truth[["shop_customer_id", "account_id"]],
                                     left_on="customer_id", right_on="shop_customer_id")
    d = df.order_dt.dt.date
    prev = df[(d >= PREV_Q[0]) & (d <= PREV_Q[1])].groupby(by).order_total.sum()
    last = df[(d >= LAST_Q[0]) & (d <= LAST_Q[1])].groupby(by).order_total.sum()
    out = pd.DataFrame({"prev": prev, "last": last}).fillna(0.0)
    out["ratio"] = out["last"] / out["prev"].where(out["prev"] > 0)
    return out


def meets_shop_fall(spend: pd.DataFrame, cfg) -> pd.Series:
    return (spend["prev"] > 0) & (spend["ratio"] <= cfg["shop"]["at_risk_fall"]["max_ratio"])


def write(payload: dict, truth: pd.DataFrame, raw_dir: Path = RAW_DIR, truth_dir: Path = GROUND_TRUTH_DIR):
    raw_dir.mkdir(parents=True, exist_ok=True)
    with open(raw_dir / ORDERS_FILE, "w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, indent=1, ensure_ascii=False)
        f.write("\n")
    _write_csv(truth, truth_dir / "shop_customers.csv")


def build(cfg, raw_dir: Path = RAW_DIR, truth_dir: Path = GROUND_TRUTH_DIR, seed: int | None = None):
    customers, lines = generate(cfg, *make_rngs(cfg["seed"] if seed is None else seed))
    payload, truth = generate_shop(cfg, customers, seed=seed)
    write(payload, truth, raw_dir, truth_dir)
    return customers, lines, payload, truth


def main() -> None:
    from generator.helpdesk import generate_tickets, meets_rise, quarter_counts
    from generator.helpdesk import TIME_FMT as HD_FMT
    from generator.telecoms import generate_telecoms, meets_fall, quarter_spend

    cfg = load_config()
    customers, lines, payload, truth = build(cfg)
    df = orders_frame(payload)
    print(f"Wrote {len(df)} orders from {truth.account_id.nunique()} customers on "
          f"{len(truth)} shop accounts (expected order value £{expected_order_value(cfg):.2f})")
    print(f"Status: {df.status.value_counts().to_dict()}")
    on_time = df[df.delivered_date.notna()]
    print(f"Delivered on or before promised date: "
          f"{(on_time.delivered_date <= on_time.promised_date).mean():.1%}")

    # revenue
    _, bills, _ = generate_telecoms(cfg, customers, lines)
    exp = expected_revenue(cfg, customers, lines).set_index("business_line")
    realised_bl5 = df.order_total.sum()
    print(f"\nBL5 revenue over the window: realised £{realised_bl5:,.0f} vs D-007 stand-in "
          f"£{exp.loc['BL5', 'expected_revenue_window']:,.0f} "
          f"({realised_bl5 / exp.loc['BL5', 'expected_revenue_window'] - 1:+.1%})")
    mix = exp.expected_revenue_window.astype(float).copy()
    mix["BL2"], mix["BL5"] = bills.total.sum(), realised_bl5
    share, target = mix / mix.sum(), pd.Series(cfg["revenue_share_targets"])
    print("Final revenue mix (BL1/BL3/BL4 modelled from CRM; BL2 billed; BL5 ordered):")
    print(pd.DataFrame({"revenue": mix.map("£{:,.0f}".format), "share": share.map("{:.1%}".format),
                        "target": target.map("{:.0%}".format),
                        "diff_pp": ((share - target) * 100).map("{:+.1f}".format)}).to_string())
    print(f"Total £{mix.sum():,.0f} over 18 months; annualised £{mix.sum() / 1.5:,.0f}")

    # at-risk shop fall
    r = cfg["shop"]["at_risk_fall"]["max_ratio"]
    combined = quarter_shop_spend(payload, truth, "account_id")
    per_account = quarter_shop_spend(payload, truth, "customer_id")
    flags_c = meets_shop_fall(combined, cfg)
    flags_a = meets_shop_fall(per_account, cfg)
    risk = customers.loc[customers.at_risk, "account_id"].tolist()
    print(f"\nAt-risk shop fall rule (combined spend): Jul-Sep <= {r} x Apr-Jun")
    print(combined.reindex(risk).assign(meets=flags_c.reindex(risk, fill_value=False)).round(2).to_string())
    print(f"At-risk customers whose largest Jul-Sep orders were dropped to meet the rule: "
          f"{truth.attrs['adjusted_at_risk']}")
    active = set(customers.loc[customers.status == "active", "account_id"])
    healthy = [a for a in combined.index if a in active and a not in risk]
    chance_c = flags_c.loc[healthy]
    acc_of = truth.set_index("shop_customer_id").account_id
    healthy_accounts = [s for s in per_account.index if acc_of[s] in healthy]
    chance_a = flags_a.loc[healthy_accounts]
    print(f"Active healthy customers meeting the rule by chance - combined: {int(chance_c.sum())} of "
          f"{len(healthy)} {list(chance_c[chance_c].index)}")
    print(f"Active healthy shop accounts meeting the rule by chance - per account: {int(chance_a.sum())} of "
          f"{len(healthy_accounts)} {[f'{s} ({acc_of[s]})' for s in chance_a[chance_a].index]}")
    sw = truth.attrs["switchers"]
    for acc in sw:
        ids = truth[truth.account_id == acc].set_index("account_kind").shop_customer_id
        print(f"Account-switch trap {acc}: primary {ids['primary']} "
              f"ratio {per_account.loc[ids['primary'], 'ratio']:.2f}, second {ids['second']} "
              f"ratio {per_account.loc[ids['second'], 'ratio']:.2f}, combined ratio {combined.loc[acc, 'ratio']:.2f}")
    print(f"At-risk customer with a second shop account: {truth.attrs['risk_with_second']}")

    # final coverage
    raw_t, truth_t = generate_tickets(cfg, customers, lines)
    raw_t = raw_t.assign(**{c: raw_t[c].map(lambda v: "" if pd.isna(v) else v.strftime(HD_FMT))
                            for c in ("opened_at", "first_responded_at", "closed_at")})
    t_flags = meets_rise(quarter_counts(raw_t, truth_t), cfg)
    _, bills, tel_truth = generate_telecoms(cfg, customers, lines)
    b_flags = meets_fall(quarter_spend(bills, tel_truth), cfg)
    cov = pd.DataFrame({"tickets_rise": [bool(t_flags.get(a, False)) for a in risk],
                        "BL2_fall": [bool(b_flags.get(a, False)) for a in risk],
                        "BL5_fall": [bool(flags_c.get(a, False)) for a in risk]}, index=risk)
    cov["spend_fall_sources"] = cov.apply(lambda x: "+".join(s for s, f in (("BL2", x.BL2_fall), ("BL5", x.BL5_fall)) if f) or "NONE", axis=1)
    print("\nFinal at-risk coverage:")
    print(cov.to_string())

    # BQ-06 combines signals: rising tickets AND at least one falling spend source.
    healthy_all = [a for a in customers.account_id if a in active and a not in risk]
    def both(a):
        return bool(t_flags.get(a, False)) and (bool(b_flags.get(a, False)) or bool(flags_c.get(a, False)))
    combo = [a for a in healthy_all if both(a)]
    print(f"\nActive healthy customers meeting BQ-06's combined pattern (ticket rise AND a spend fall) "
          f"by chance: {len(combo)} of {len(healthy_all)} {combo}")
    print(f"All at-risk customers meet the combined pattern: {all(both(a) for a in risk)}")


if __name__ == "__main__":
    main()
