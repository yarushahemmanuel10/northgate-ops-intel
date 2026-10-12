"""Helpdesk tickets (clean) and their ground truth.

Run:  python -m generator.helpdesk
Writes data/raw/helpdesk_tickets.csv (what the helpdesk tool exports) and
data/ground_truth/tickets.csv (true customer and topic per ticket), and prints SLA breach
rates, the open-ticket count and the at-risk ticket rise.

Rules (fact sheet §1.3, §1.7, §2.2):
- Only customers with a BL1 (managed IT support) contract line raise tickets, between the
  line's start and its end (or the as-of date).
- Times are elapsed hours (D-001). Open = opened before the as-of moment, closed_at empty.
- The raw file carries the customer name as typed, never an ACC- ID (QI-02).
- Deliberate mess (QI-03, QI-05 to QI-08) is added in a later step, not here.
"""
from __future__ import annotations

import datetime as dt
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from generator.config import PRIORITIES, load_config
from generator.customers import GROUND_TRUTH_DIR, _write_csv, generate, make_rngs
from generator.streams import named_stream
from generator.ticket_text import TOPICS, ticket_text

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
RAW_COLUMNS = ["ticket_id", "opened_at", "first_responded_at", "closed_at", "priority", "category",
               "customer_name", "agent", "description", "resolution_notes", "kb_article_id"]
TIME_FMT = "%Y-%m-%d %H:%M"
PREV_Q = (dt.date(2026, 4, 1), dt.date(2026, 6, 30))
LAST_Q = (dt.date(2026, 7, 1), dt.date(2026, 9, 30))
DAYS_PER_MONTH = 30.44


def as_of_moment(cfg: dict[str, Any]) -> dt.datetime:
    return dt.datetime.combine(cfg["as_of_date"] + dt.timedelta(days=1), dt.time())


def _days(lo: dt.date, hi: dt.date) -> list[dt.date]:
    return [lo + dt.timedelta(days=i) for i in range((hi - lo).days + 1)] if hi >= lo else []


def _support_windows(cfg, customers, lines) -> dict[str, tuple[dt.date, dt.date]]:
    """BL1 customers -> first and last day on which they can open a ticket."""
    bl1 = lines[lines.business_line == "BL1"].set_index("account_id")
    out = {}
    for acc, row in bl1.iterrows():
        first = max(cfg["window"]["start"], row.contract_start)
        last = cfg["as_of_date"] if row.end_date is None else row.end_date - dt.timedelta(days=1)
        if last >= first:
            out[acc] = (first, last)
    return out


def _open_time(day_pool: list[dt.date], cfg, rng: np.random.Generator) -> dt.datetime:
    """A ticket opening time: mostly Mon-Fri working hours, sometimes out of hours."""
    h0, h1 = cfg["tickets"]["working_hours"]
    if rng.random() < cfg["tickets"]["working_hours_share"]:
        weekdays = [d for d in day_pool if d.weekday() < 5] or day_pool
        day = weekdays[int(rng.integers(len(weekdays)))]
        minute = int(rng.integers(h0 * 60, h1 * 60))
    else:
        day = day_pool[int(rng.integers(len(day_pool)))]
        if day.weekday() >= 5:
            minute = int(rng.integers(0, 24 * 60))
        else:   # weekday evening or early morning
            out_of_hours = list(range(0, h0 * 60)) + list(range(h1 * 60, 24 * 60))
            minute = out_of_hours[int(rng.integers(len(out_of_hours)))]
    return dt.datetime.combine(day, dt.time()) + dt.timedelta(minutes=minute)


def _topic_weights(cfg) -> dict[str, tuple[list[str], np.ndarray]]:
    """For each priority, the topics that allow it, weighted by category weight."""
    cat_w = cfg["tickets"]["category_weights"]
    per_cat = pd.Series([t["category"] for t in TOPICS.values()]).value_counts()
    out = {}
    for p in PRIORITIES:
        ids = [k for k, t in TOPICS.items() if p in t["priorities"]]
        w = np.array([cat_w[TOPICS[k]["category"]] / per_cat[TOPICS[k]["category"]] for k in ids])
        out[p] = (ids, w / w.sum())
    return out


def generate_tickets(cfg: dict[str, Any], customers: pd.DataFrame, lines: pd.DataFrame,
                     seed: int | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    t = cfg["tickets"]
    root = named_stream(cfg["seed"] if seed is None else seed, "helpdesk")
    counts_rng, times_rng, sla_rng, text_rng, open_rng = root.spawn(5)
    # Topic and agent choices are kept apart from wording, so editing templates never
    # changes which topic or agent a ticket has (D-008).
    topic_rng, agent_rng = text_rng.spawn(2)
    windows = _support_windows(cfg, customers, lines)
    at_risk = set(customers.loc[customers.at_risk, "account_id"])
    names = customers.set_index("account_id").company_name

    # --- how many tickets each customer opens, and on which days ---------------------
    rise = t["at_risk_rise"]
    plan: list[tuple[str, list[dt.date]]] = []      # (account, day pool) per ticket
    base_pools = {}
    for acc, (first, last) in windows.items():
        days = _days(first, last)
        if acc in at_risk:
            days = [d for d in days if not (PREV_Q[0] <= d <= LAST_Q[1])]
        base_pools[acc] = days
    rates = {acc: counts_rng.gamma(t["customer_rate_shape"], 1 / t["customer_rate_shape"])
             for acc in windows}
    prev_mean = sum(rise["previous_quarter_range"]) / 2
    explicit = len(at_risk) * (prev_mean + max(rise["min_last_quarter"], math.ceil(rise["ratio"] * prev_mean))
                               + sum(rise["extra_last_quarter_range"]) / 2)
    exposure = sum(rates[a] * len(base_pools[a]) / DAYS_PER_MONTH for a in windows)
    monthly_rate = (cfg["volumes"]["tickets"] - explicit) / exposure

    for acc in windows:
        n = counts_rng.poisson(monthly_rate * rates[acc] * len(base_pools[acc]) / DAYS_PER_MONTH)
        plan += [(acc, base_pools[acc])] * int(n)
        if acc in at_risk:
            prev = int(counts_rng.integers(rise["previous_quarter_range"][0], rise["previous_quarter_range"][1] + 1))
            last = max(rise["min_last_quarter"], math.ceil(rise["ratio"] * prev)) + \
                int(counts_rng.integers(rise["extra_last_quarter_range"][0], rise["extra_last_quarter_range"][1] + 1))
            plan += [(acc, _days(*PREV_Q))] * prev + [(acc, _days(*LAST_Q))] * last

    # --- attributes of each ticket -------------------------------------------------------
    topics = _topic_weights(cfg)
    prios, mix = list(t["priority_mix"]), np.array(list(t["priority_mix"].values()))
    agents = t["agents"]
    # Priorities first, then exactly round(rate x n) breaches per priority, so the generated
    # breach rates match the config rather than varying with the seed (D-011).
    n_tickets = len(plan)
    priority = sla_rng.choice(prios, size=n_tickets, p=mix)
    res_breach = np.zeros(n_tickets, dtype=bool)
    for p in prios:
        idx = np.flatnonzero(priority == p)
        k = round(t["resolution_breach_rate"][p] * len(idx))
        res_breach[sla_rng.choice(idx, size=k, replace=False)] = True
    resp_breach = np.zeros(n_tickets, dtype=bool)
    resp_breach[sla_rng.choice(n_tickets, size=round(t["response_breach_rate"] * n_tickets), replace=False)] = True

    rows = []
    for i, (acc, pool) in enumerate(plan):
        opened = _open_time(pool, cfg, times_rng)
        p = str(priority[i])
        target = t["sla_hours"][p]
        if resp_breach[i]:
            resp_h = target["response"] * sla_rng.uniform(1.05, 3.0)
        else:
            resp_h = target["response"] * sla_rng.uniform(0.05, 0.95)
        if res_breach[i]:
            res_h = target["resolution"] * sla_rng.uniform(1.05, 3.0)
        else:
            res_h = target["resolution"] * sla_rng.uniform(0.15, 0.95)
        res_h = max(res_h, resp_h + 0.25)
        ids, w = topics[p]
        topic = str(topic_rng.choice(ids, p=w))
        desc, res = ticket_text(topic, text_rng)
        rows.append({
            "account_id": acc, "topic_id": topic, "priority": p,
            "opened_at": opened,
            "first_responded_at": opened + dt.timedelta(minutes=round(resp_h * 60)),
            "closed_at": opened + dt.timedelta(minutes=round(res_h * 60)),
            "category": TOPICS[topic]["category"],
            "customer_name": names[acc],
            "agent": agents[int(agent_rng.integers(len(agents)))],
            "description": desc, "resolution_notes": res,
        })
    df = pd.DataFrame(rows).sort_values(["opened_at", "account_id"], kind="stable").reset_index(drop=True)
    df.insert(0, "ticket_id", [f"INC-{i:05d}" for i in range(1, len(df) + 1)])

    # --- open tickets at the as-of moment -------------------------------------------------
    moment = as_of_moment(cfg)
    df.loc[df.closed_at > moment, "closed_at"] = pd.NaT
    natural_open = int(df.closed_at.isna().sum())
    need = cfg["volumes"]["open_tickets_at_as_of"] - natural_open
    if need < 0:
        raise ValueError(f"{natural_open} tickets are naturally open, more than the configured "
                         f"{cfg['volumes']['open_tickets_at_as_of']}")
    closed_accounts = set(customers.loc[customers.status == "closed", "account_id"])
    candidates = df.index[df.closed_at.notna() & (df.opened_at >= dt.datetime(2026, 9, 1))
                          & df.priority.isin(["P3", "P4"]) & ~df.account_id.isin(closed_accounts)]
    stuck = open_rng.choice(candidates, size=need, replace=False)
    df.loc[stuck, "closed_at"] = pd.NaT
    df.loc[df.first_responded_at > moment, "first_responded_at"] = pd.NaT

    raw = df[RAW_COLUMNS[:-1]].copy()
    raw["kb_article_id"] = ""            # filled from the knowledge base in a later step
    truth = df[["ticket_id", "account_id", "topic_id"]].copy()
    return raw, truth


# --- measurements used by tests and the report ------------------------------------------

def _parse(raw: pd.DataFrame) -> pd.DataFrame:
    out = raw.copy()
    for col in ("opened_at", "first_responded_at", "closed_at"):
        out[col] = pd.to_datetime(out[col], format=TIME_FMT)
    return out


def breach_rates(cfg, raw: pd.DataFrame) -> pd.DataFrame:
    """Resolution and response breach rates per priority, closed tickets only (D-001)."""
    df = _parse(raw)
    closed = df[df.closed_at.notna()].copy()
    target = pd.DataFrame(cfg["tickets"]["sla_hours"]).T
    closed["res_h"] = (closed.closed_at - closed.opened_at).dt.total_seconds() / 3600
    closed["resp_h"] = (closed.first_responded_at - closed.opened_at).dt.total_seconds() / 3600
    closed["res_breach"] = closed.res_h > closed.priority.map(target.resolution)
    closed["resp_breach"] = closed.resp_h > closed.priority.map(target.response)
    g = closed.groupby("priority")
    return pd.DataFrame({
        "closed_tickets": g.size(),
        "resolution_breach": g.res_breach.mean(),
        "target_resolution_breach": pd.Series(cfg["tickets"]["resolution_breach_rate"]),
        "response_breach": g.resp_breach.mean(),
    })


def quarter_counts(raw: pd.DataFrame, truth: pd.DataFrame) -> pd.DataFrame:
    """Tickets per customer in Apr-Jun 2026 (prev) and Jul-Sep 2026 (last)."""
    df = _parse(raw).merge(truth, on="ticket_id")
    d = df.opened_at.dt.date
    prev = df[(d >= PREV_Q[0]) & (d <= PREV_Q[1])].groupby("account_id").size()
    last = df[(d >= LAST_Q[0]) & (d <= LAST_Q[1])].groupby("account_id").size()
    out = pd.DataFrame({"prev": prev, "last": last}).fillna(0).astype(int)
    return out


def meets_rise(counts: pd.DataFrame, cfg) -> pd.Series:
    r = cfg["tickets"]["at_risk_rise"]
    return (counts["last"] >= r["min_last_quarter"]) & (counts["last"] >= r["ratio"] * counts["prev"])


def write(cfg, raw: pd.DataFrame, truth: pd.DataFrame, raw_dir: Path = RAW_DIR,
          truth_dir: Path = GROUND_TRUTH_DIR) -> None:
    out = raw.copy()
    for col in ("opened_at", "first_responded_at", "closed_at"):
        out[col] = out[col].map(lambda v: "" if pd.isna(v) else v.strftime(TIME_FMT))
    _write_csv(out, raw_dir / "helpdesk_tickets.csv")
    _write_csv(truth, truth_dir / "tickets.csv")


def build(cfg, raw_dir: Path = RAW_DIR, truth_dir: Path = GROUND_TRUTH_DIR, seed: int | None = None):
    customers, lines = generate(cfg, *make_rngs(cfg["seed"] if seed is None else seed))
    raw, truth = generate_tickets(cfg, customers, lines, seed=seed)
    write(cfg, raw, truth, raw_dir, truth_dir)
    return customers, lines, raw, truth


def main() -> None:
    cfg = load_config()
    customers, lines, raw, truth = build(cfg)
    raw_s = raw.copy()
    for col in ("opened_at", "first_responded_at", "closed_at"):
        raw_s[col] = raw_s[col].map(lambda v: "" if pd.isna(v) else v.strftime(TIME_FMT))
    print(f"Wrote {len(raw)} tickets for {truth.account_id.nunique()} customers")
    print(f"Open at the as-of moment: {int(raw.closed_at.isna().sum())}")
    opened = pd.to_datetime(raw.opened_at)
    h0, h1 = cfg["tickets"]["working_hours"]
    in_hours = (opened.dt.weekday < 5) & (opened.dt.hour >= h0) & (opened.dt.hour < h1)
    print(f"Opened Mon-Fri {h0}:00-{h1}:00: {in_hours.mean():.1%}")
    print("\nSLA breach rates (closed tickets, elapsed hours):")
    br = breach_rates(cfg, raw_s)
    print(br.to_string(formatters={c: "{:.1%}".format for c in br.columns if c != "closed_tickets"}))
    print(f"Response breach overall: {(br.response_breach * br.closed_tickets).sum() / br.closed_tickets.sum():.1%}"
          f" (target {cfg['tickets']['response_breach_rate']:.0%})")

    counts = quarter_counts(raw_s, truth)
    flags = meets_rise(counts, cfg)
    r = cfg["tickets"]["at_risk_rise"]
    risk_ids = customers.loc[customers.at_risk, "account_id"]
    print(f"\nAt-risk rise rule: Jul-Sep >= {r['ratio']} x Apr-Jun and Jul-Sep >= {r['min_last_quarter']}")
    print(counts.reindex(risk_ids, fill_value=0).assign(meets=flags.reindex(risk_ids, fill_value=False)).to_string())
    healthy = customers.loc[~customers.at_risk, "account_id"]
    chance = flags.reindex(healthy, fill_value=False)
    print(f"Customers not at risk that meet the rule by chance: {int(chance.sum())} "
          f"of {int(counts.index.isin(healthy).sum())} with tickets {list(chance[chance].index)}")

    closed = customers[customers.status == "closed"].set_index("account_id").closed_date
    df = _parse(raw_s).merge(truth, on="ticket_id")
    df = df[df.account_id.isin(closed.index)]
    cd = df.account_id.map(closed).map(lambda d: dt.datetime.combine(d, dt.time()))
    after = int((df.opened_at >= cd).sum())
    still_open = df[(df.opened_at < cd) & (df.closed_at.isna() | (df.closed_at > cd))]
    print(f"\nClosed customers: tickets opened on/after closing date: {after}; "
          f"tickets still open on the closing date: {len(still_open)} {list(still_open.ticket_id)}")


if __name__ == "__main__":
    main()
