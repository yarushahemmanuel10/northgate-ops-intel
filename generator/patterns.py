"""The planted BQ-06 pattern before and after cleaning (Phase 1, step 1.9 rule 6).

Three views of the same data:
- clean:   the data before any quality problems were injected (what is true)
- cleaned: the messy data cleaned correctly, using the answer key where Phase 2 would use
           entity resolution: names mapped to the true account, duplicate orders and bills
           removed, cancelled and refunded orders excluded, credit notes applied, non-ISO
           dates parsed by each source's convention
- naive:   the messy data used as it comes: names matched to the CRM only when spelled
           exactly the same, no deduplication, cancelled and refunded orders counted, rows
           with non-ISO dates dropped, each shop account treated separately, telecoms
           unmatched (billing-system names never equal CRM names)

BQ-06 pattern: ticket rise (Jul-Sep >= 2 x Apr-Jun and >= 8) AND a spend fall (BL2 bills
Jul-Sep <= 0.85 x Apr-Jun, or shop spend Jul-Sep <= 0.4 x Apr-Jun), active customers only.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

import pandas as pd

PREV = ("2026-04", "2026-06")
LAST = ("2026-07", "2026-09")


def _month_from_ts(s: str) -> str | None:
    for fmt in ("%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M"):
        try:
            return dt.datetime.strptime(s, fmt).strftime("%Y-%m")
        except (ValueError, TypeError):
            pass
    return None


def _month_from_bill(s: str) -> str | None:
    for fmt in ("%Y-%m", "%b-%y", "%m/%Y"):
        try:
            return dt.datetime.strptime(s, fmt).strftime("%Y-%m")
        except (ValueError, TypeError):
            pass
    return None


def _quarters(df: pd.DataFrame, key: str, value: str | None) -> pd.DataFrame:
    prev = df[df.month.between(*PREV)]
    last = df[df.month.between(*LAST)]
    agg = (lambda g: g.size()) if value is None else (lambda g: g[value].sum())
    out = pd.DataFrame({"prev": agg(prev.groupby(key)), "last": agg(last.groupby(key))}).fillna(0)
    out["ratio"] = out["last"] / out["prev"].where(out["prev"] > 0)
    return out


def metrics(cfg: dict[str, Any], tables: dict, context: dict, mode: str, known: pd.DataFrame | None = None) -> dict:
    """Per-customer quarter metrics for tickets, BL2 bills and shop spend."""
    customers = context["customers"]
    crm_names = {r["company_name"]: r["account_id"] for r in tables["crm_accounts"].rows}
    hd = pd.DataFrame(tables["helpdesk"].rows)
    bills = pd.DataFrame(tables["telecoms_bills"].rows)
    orders = pd.DataFrame(tables["shop_orders"].rows)
    tel_truth = context["telecoms_truth"].set_index("account_no").account_id
    shop_truth = context["shop_truth"].set_index("shop_customer_id").account_id

    if mode == "naive":
        hd["month"] = hd.opened_at.map(lambda s: s[:7] if s[:4].isdigit() else None)
        hd["acc"] = hd.customer_name.map(crm_names)
        tickets = _quarters(hd.dropna(subset=["acc", "month"]), "acc", None)
        bl2 = pd.DataFrame(columns=["prev", "last", "ratio"])          # cannot be linked by name
        orders["month"] = orders.order_date.str[:7]
        orders["acc"] = orders.company_name.map(crm_names)
        shop = _quarters(orders.dropna(subset=["acc"]), "customer_id", "order_total")
        shop["acc"] = orders.dropna(subset=["acc"]).groupby("customer_id").acc.first()
    else:
        hd["month"] = hd.opened_at.map(_month_from_ts)
        hd["acc"] = hd.ticket_id.map(context["tickets_truth"].set_index("ticket_id").account_id)
        tickets = _quarters(hd, "acc", None)
        if mode == "cleaned":
            bills = bills.drop_duplicates()                              # re-run export rows are identical
            dup_orders = set(known[(known.qi == "QI-04") & (known.table == "shop_orders")].record_key)
            orders = orders[~orders.order_id.isin(dup_orders) & ~orders.status.isin(["cancelled", "refunded"])]
        bills["month"] = bills.bill_month.map(_month_from_bill)
        bills["acc"] = bills.account_no.map(tel_truth)
        bl2 = _quarters(bills, "acc", "total")
        orders = orders.assign(month=orders.order_date.map(_month_from_ts),
                               acc=orders.customer_id.map(shop_truth))
        shop = _quarters(orders, "acc", "order_total")
    return {"tickets": tickets, "bl2": bl2, "shop": shop}


def flags(cfg: dict[str, Any], m: dict, active: set[str]) -> pd.DataFrame:
    rise = cfg["tickets"]["at_risk_rise"]
    t = m["tickets"]
    t_flag = (t["last"] >= rise["min_last_quarter"]) & (t["last"] >= rise["ratio"] * t["prev"])
    b = m["bl2"]
    b_flag = (b["prev"] > 0) & (b["ratio"] <= cfg["telecoms"]["at_risk_fall"]["max_ratio"])
    s = m["shop"]
    s_flag = (s["prev"] > 0) & (s["ratio"] <= cfg["shop"]["at_risk_fall"]["max_ratio"])
    if "acc" in s.columns:                                  # naive: per shop account -> CRM account
        s_flag = s_flag.groupby(s["acc"]).any()
    ids = sorted(set(t.index) | set(b.index) | set(s_flag.index))
    out = pd.DataFrame(index=ids)
    out["ticket_rise"] = t_flag.reindex(ids, fill_value=False)
    out["bl2_fall"] = b_flag.reindex(ids, fill_value=False)
    out["shop_fall"] = s_flag.reindex(ids, fill_value=False)
    out["pattern"] = out.ticket_rise & (out.bl2_fall | out.shop_fall)
    out["active"] = [i in active for i in ids]
    return out


def compare(cfg, clean_tables, messy_tables, known, context) -> dict:
    customers = context["customers"]
    active = set(customers.loc[customers.status == "active", "account_id"])
    risk = sorted(customers.loc[customers.at_risk, "account_id"])
    views = {mode: metrics(cfg, tabs, context, mode, known)
             for mode, tabs in (("clean", clean_tables), ("cleaned", messy_tables), ("naive", messy_tables))}
    fl = {mode: flags(cfg, m, active) for mode, m in views.items()}
    flagged = {mode: sorted(f.index[f.pattern & f.active]) for mode, f in fl.items()}

    def ratio(mode, kind, acc):
        df = views[mode][kind]
        if kind == "shop" and "acc" in df.columns:        # naive shop: worst account ratio
            sub = df[df["acc"] == acc]
            return round(float(sub.ratio.min()), 2) if len(sub) and sub.ratio.notna().any() else None
        if acc in df.index and pd.notna(df.at[acc, "ratio"]):
            return round(float(df.at[acc, "ratio"]), 2)
        return None

    table = pd.DataFrame([{
        "account_id": a,
        "tickets naive": ratio("naive", "tickets", a), "tickets cleaned": ratio("cleaned", "tickets", a),
        "BL2 naive": ratio("naive", "bl2", a), "BL2 cleaned": ratio("cleaned", "bl2", a),
        "shop naive": ratio("naive", "shop", a), "shop cleaned": ratio("cleaned", "shop", a),
        "flag naive": a in flagged["naive"], "flag cleaned": a in flagged["cleaned"],
    } for a in risk]).set_index("account_id")
    return {"views": views, "flags": fl, "flagged": flagged, "at_risk_table": table, "risk": risk}
