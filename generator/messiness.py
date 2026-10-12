"""Deliberate data-quality problems (QI-01 to QI-11) and their answer key.

Run:  python -m generator.messiness
Builds the clean sources in memory (the same data the generators write), applies the
quality problems, writes the messy raw files over data/raw/, and records every change in
data/ground_truth/known_issues.csv.

Answer key: each log entry is a change to one cell, an added row or a removed row, with
the row's position at the time and typed JSON for the old and new values. Undoing the
entries in reverse order restores the clean tables exactly (tested byte for byte).

Every QI code draws from its own named stream ("mess.QI-01", ...), so changing one rate
never moves another problem's records (D-008).
"""
from __future__ import annotations

import copy
import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from generator.config import load_config
from generator.customers import ROOT, _write_csv, generate, make_rngs, write_ground_truth
from generator.helpdesk import generate_tickets
from generator.kb_links import apply_kb_links
from generator.names import LEGAL_SUFFIX_RE, core_name, name_variant
from generator.streams import named_stream

TIME_FMT = "%Y-%m-%d %H:%M"
RAW_DIR = ROOT / "data" / "raw"                 # the only writer of data/raw
GROUND_TRUTH_DIR = ROOT / "data" / "ground_truth"
KNOWN_ISSUES_COLUMNS = ["issue_id", "qi", "table", "op", "position", "record_key", "field",
                        "original_value", "new_value", "original_json", "new_json", "note"]


# --- typed values in the answer key -----------------------------------------------------

def _wrap(v: Any) -> Any:
    if isinstance(v, dt.datetime):
        return {"dt": v.isoformat()}
    if isinstance(v, dt.date):
        return {"d": v.isoformat()}
    if isinstance(v, dict):
        return {"o": {k: _wrap(x) for k, x in v.items()}}
    if isinstance(v, list):
        return {"l": [_wrap(x) for x in v]}
    if isinstance(v, np.integer):
        return {"v": int(v)}
    if isinstance(v, np.floating):
        return {"v": float(v)}
    if isinstance(v, np.bool_):
        return {"v": bool(v)}
    return {"v": v}


def _unwrap(w: dict) -> Any:
    if "dt" in w:
        return dt.datetime.fromisoformat(w["dt"])
    if "d" in w:
        return dt.date.fromisoformat(w["d"])
    if "o" in w:
        return {k: _unwrap(x) for k, x in w["o"].items()}
    if "l" in w:
        return [_unwrap(x) for x in w["l"]]
    return w["v"]


def encode(v: Any) -> str:
    """Typed JSON, so dates, numbers, rows and order lines come back exactly as they were."""
    return json.dumps(_wrap(v), ensure_ascii=False)


def decode(s: str) -> Any:
    return _unwrap(json.loads(s))


def _show(v: Any) -> str:
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, default=str)[:120]
    return "" if v is None else str(v)


# --- tables and the change log -----------------------------------------------------------

@dataclass
class Table:
    name: str
    key: str
    rows: list[dict]

    def find(self, key_value) -> int:
        return next(i for i, r in enumerate(self.rows) if r[self.key] == key_value)


@dataclass
class Mess:
    tables: dict[str, Table]
    log: list[dict] = field(default_factory=list)

    def _entry(self, qi, table, op, pos, fld, old, new, note):
        t = self.tables[table]
        key = t.rows[pos][t.key] if op != "remove_row" and pos < len(t.rows) else (old or {}).get(t.key, "")
        self.log.append({"qi": qi, "table": table, "op": op, "position": pos,
                         "record_key": key if op != "remove_row" else old[t.key], "field": fld,
                         "original_value": _show(old), "new_value": _show(new),
                         "original_json": encode(old), "new_json": encode(new), "note": note})

    def change(self, qi: str, table: str, pos: int, fld: str, new: Any, note: str = "") -> None:
        t = self.tables[table]
        old = t.rows[pos][fld]
        if old == new:
            raise ValueError(f"{qi}: change to {table}[{pos}].{fld} does not change the value")
        t.rows[pos][fld] = new
        self._entry(qi, table, "change", pos, fld, old, new, note)

    def add_row(self, qi: str, table: str, pos: int, row: dict, note: str = "") -> None:
        self.tables[table].rows.insert(pos, row)
        self._entry(qi, table, "add_row", pos, "", None, row, note)

    def remove_row(self, qi: str, table: str, pos: int, note: str = "") -> None:
        row = self.tables[table].rows.pop(pos)
        self._entry(qi, table, "remove_row", pos, "", row, None, note)

    def annotate(self, qi: str, table: str, pos: int, fld: str, text: str, note: str) -> None:
        """Record a span of inserted text (e.g. personal data) without changing anything.

        Undo ignores these entries; they exist so masking can be scored against them."""
        t = self.tables[table]
        self.log.append({"qi": qi, "table": table, "op": "pii_span", "position": pos,
                         "record_key": t.rows[pos][t.key], "field": fld, "original_value": "",
                         "new_value": text, "original_json": encode(None), "new_json": encode(text),
                         "note": note})

    def known_issues(self) -> pd.DataFrame:
        df = pd.DataFrame(self.log, columns=KNOWN_ISSUES_COLUMNS[1:])
        df.insert(0, "issue_id", [f"KI-{i:05d}" for i in range(1, len(df) + 1)])
        return df


def undo(tables: dict[str, Table], known: pd.DataFrame) -> dict[str, Table]:
    """Reverse every logged change, newest first. Uses only the answer key."""
    out = copy.deepcopy(tables)
    for e in known.iloc[::-1].itertuples():
        rows = out[e.table].rows
        pos = int(e.position)
        if e.op == "change":
            rows[pos][e.field] = decode(e.original_json)
        elif e.op == "add_row":
            rows.pop(pos)
        elif e.op == "remove_row":
            rows.insert(pos, decode(e.original_json))
    return out


# --- clean sources as tables ---------------------------------------------------------------

def clean_tables(cfg: dict[str, Any], seed: int | None = None) -> tuple[dict[str, Table], dict]:
    from generator.crm import ACCOUNT_COLUMNS, LINE_COLUMNS
    from generator.shop import generate_shop
    from generator.telecoms import generate_telecoms

    customers, lines = generate(cfg, *make_rngs(cfg["seed"] if seed is None else seed))
    raw_t, truth_t = generate_tickets(cfg, customers, lines, seed=seed)
    raw_t, truth_t = apply_kb_links(cfg, raw_t, truth_t, seed=seed)
    for col in ("opened_at", "first_responded_at", "closed_at"):
        raw_t[col] = raw_t[col].map(lambda v: "" if pd.isna(v) else v.strftime(TIME_FMT))
    contracts, bills, truth_tel = generate_telecoms(cfg, customers, lines, seed=seed)
    payload, truth_shop = generate_shop(cfg, customers, seed=seed)

    def rows(df: pd.DataFrame, cols) -> list[dict]:
        return [{c: (None if (v is None or (isinstance(v, float) and np.isnan(v))) else
                     (v.item() if hasattr(v, "item") else v)) for c, v in zip(cols, r)}
                for r in df[list(cols)].itertuples(index=False)]

    tables = {
        "crm_accounts": Table("crm_accounts", "account_id", rows(customers, ACCOUNT_COLUMNS)),
        "crm_lines": Table("crm_lines", "line_id", rows(lines, LINE_COLUMNS)),
        "helpdesk": Table("helpdesk", "ticket_id", rows(raw_t, raw_t.columns)),
        "telecoms_contracts": Table("telecoms_contracts", "contract_ref", rows(contracts, contracts.columns)),
        "telecoms_bills": Table("telecoms_bills", "bill_id", rows(bills, bills.columns)),
        "shop_orders": Table("shop_orders", "order_id", copy.deepcopy(payload["orders"])),
    }
    context = {"customers": customers, "lines": lines, "tickets_truth": truth_t,
               "contract_account": dict(zip(contracts.contract_ref, contracts.account_no)),
               "telecoms_contracts_clean": contracts.to_dict("records"),
               "telecoms_bills_clean": bills.to_dict("records"),
               "telecoms_truth": truth_tel, "shop_truth": truth_shop, "payload": payload}
    return tables, context


def write_tables(tables: dict[str, Table], context: dict, raw_dir: Path = RAW_DIR) -> None:
    from generator.crm import WORKBOOK_NAME, write_crm_workbook
    from generator.shop import ORDERS_FILE
    from generator.telecoms import write_raw

    write_crm_workbook(pd.DataFrame(tables["crm_accounts"].rows), pd.DataFrame(tables["crm_lines"].rows),
                       raw_dir / WORKBOOK_NAME)
    _write_csv(pd.DataFrame(tables["helpdesk"].rows), raw_dir / "helpdesk_tickets.csv")
    write_raw(pd.DataFrame(tables["telecoms_contracts"].rows), pd.DataFrame(tables["telecoms_bills"].rows), raw_dir)
    payload = dict(context["payload"])
    payload["orders"] = tables["shop_orders"].rows
    raw_dir.mkdir(parents=True, exist_ok=True)
    with open(raw_dir / ORDERS_FILE, "w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, indent=1, ensure_ascii=False)
        f.write("\n")


# --- helpers ------------------------------------------------------------------------------------

def _pick(rng: np.random.Generator, n: int, k: int) -> list[int]:
    """k distinct positions out of n, returned in increasing order."""
    k = min(k, n)
    return sorted(int(i) for i in rng.choice(n, size=k, replace=False))


def _uk_date(d: dt.date, rng: np.random.Generator) -> str:
    """CRM convention for non-ISO dates: UK day-first, dd/mm/yyyy or 'd Mon yyyy'."""
    return d.strftime("%d/%m/%Y") if rng.random() < 0.6 else f"{d.day} {d.strftime('%b')} {d.year}"


# --- Part A: QI-01 to QI-06 ----------------------------------------------------------------------

def apply_part_a(cfg: dict[str, Any], mess: Mess, context: dict, seed: int | None = None) -> dict:
    seed = cfg["seed"] if seed is None else seed
    qi = cfg["quality_issues"]
    t = mess.tables
    customers = context["customers"]
    real_names = {n.lower(): a for a, n in zip(customers.account_id, customers.company_name)}
    names = customers.set_index("account_id").company_name
    report: dict[str, Any] = {"collisions_rejected": []}

    # ---- QI-01 name variants ----------------------------------------------------------
    rng = named_stream(seed, "mess.QI-01")
    q1 = qi["QI-01_name_variants"]
    kinds = list(q1["variant_weights"])
    weights = np.array(list(q1["variant_weights"].values()))

    def variant_for(acc: str, allowed: list[str], p=None) -> str:
        original = names[acc]
        for _ in range(20):
            kind = str(rng.choice(allowed, p=p))
            v = name_variant(original, kind, rng)
            owner = real_names.get(v.lower())
            if owner is None or owner == acc:
                return v
            report["collisions_rejected"].append((acc, v, owner))
        raise ValueError(f"could not find a non-colliding variant for {acc}")

    tickets_truth = context["tickets_truth"].set_index("ticket_id").account_id
    hd = t["helpdesk"].rows
    for pos in _pick(rng, len(hd), round(q1["helpdesk"] * len(hd))):
        acc = tickets_truth[hd[pos]["ticket_id"]]
        mess.change("QI-01", "helpdesk", pos, "customer_name", variant_for(acc, kinds, weights))

    shop_truth = context["shop_truth"]
    seconds = shop_truth[shop_truth.account_kind == "second"]
    firsts = shop_truth[shop_truth.account_kind == "primary"]
    n_target = round(q1["shop"] * len(shop_truth))
    chosen = list(seconds.shop_customer_id) + \
        [int(x) for x in rng.choice(sorted(firsts.shop_customer_id), size=n_target - len(seconds), replace=False)]
    acc_of = shop_truth.set_index("shop_customer_id").account_id
    shop_names = {}
    for sid in sorted(chosen):
        shop_names[sid] = variant_for(acc_of[sid], kinds, weights)
    for pos, o in enumerate(t["shop_orders"].rows):
        if o["customer_id"] in shop_names:
            mess.change("QI-01", "shop_orders", pos, "company_name", shop_names[o["customer_id"]],
                        note=f"shop account {o['customer_id']} name set at sign-up")
    report["shop_accounts_renamed"] = len(shop_names)

    from generator.telecoms import billing_style
    tel_truth = context["telecoms_truth"].set_index("account_no").account_id
    tc = t["telecoms_contracts"].rows
    billing_names = {billing_style(n).lower() for n in names}
    for pos in _pick(rng, len(tc), round(q1["telecoms_extra"] * len(tc))):
        acc = tel_truth[tc[pos]["account_no"]]
        for _ in range(20):
            kind = str(rng.choice(q1["telecoms_variant_kinds"]))
            v = billing_style(name_variant(names[acc], kind, rng))
            if v != tc[pos]["account_name"] and (v.lower() not in billing_names
                                                 or v.lower() == billing_style(names[acc]).lower()):
                break
            report["collisions_rejected"].append((acc, v, "another billing-style name"))
        mess.change("QI-01", "telecoms_contracts", pos, "account_name", v)

    # ---- QI-03 mixed date formats -------------------------------------------------------
    rng = named_stream(seed, "mess.QI-03")
    q3 = qi["QI-03_mixed_dates"]
    cells = [(tbl, pos, f) for tbl, fields in (("crm_accounts", ["customer_since", "closed_date"]),
                                               ("crm_lines", ["contract_start", "renewal_date", "end_date"]))
             for pos, r in enumerate(t[tbl].rows) for f in fields if r[f] is not None]
    non_iso = []
    report["crm_date_cells"] = len(cells)
    for i in _pick(rng, len(cells), round(q3["crm"] * len(cells))):
        tbl, pos, f = cells[i]
        d = t[tbl].rows[pos][f]
        mess.change("QI-03", tbl, pos, f, _uk_date(d, rng), note="CRM convention: UK day-first")
        non_iso.append(d)
    report["crm_non_iso_dates"] = len(non_iso)
    report["crm_ambiguous_share"] = float(np.mean([d.day <= 12 for d in non_iso])) if non_iso else 0.0

    bills = t["telecoms_bills"].rows
    for pos in _pick(rng, len(bills), round(q3["telecoms_bill_month"] * len(bills))):
        m = dt.date.fromisoformat(bills[pos]["bill_month"] + "-01")
        new = m.strftime("%b-%y") if rng.random() < 0.5 else m.strftime("%m/%Y")
        mess.change("QI-03", "telecoms_bills", pos, "bill_month", new, note="telecoms convention: Mon-yy or mm/yyyy")

    for pos in _pick(rng, len(hd), round(q3["helpdesk"] * len(hd))):
        for f in ("opened_at", "first_responded_at", "closed_at"):
            if hd[pos][f]:
                v = dt.datetime.strptime(hd[pos][f], TIME_FMT).strftime("%d/%m/%Y %H:%M")
                mess.change("QI-03", "helpdesk", pos, f, v, note="helpdesk convention: dd/mm/yyyy HH:MM")

    # ---- QI-04 duplicates --------------------------------------------------------------------
    rng = named_stream(seed, "mess.QI-04")
    q4 = qi["QI-04_duplicates"]
    accounts = t["crm_accounts"].rows
    next_id = len(customers) + 1
    dup_map = []
    for pos in _pick(rng, len(accounts), q4["crm_duplicate_accounts"]):
        src = dict(accounts[pos])
        kind = str(rng.choice(q4["crm_duplicate_name_kinds"]))
        src_name = names[src["account_id"]]
        new_name = name_variant(src_name, kind, rng)
        dup = dict(src, account_id=f"ACC-{next_id:04d}", company_name=new_name)
        mess.add_row("QI-04", "crm_accounts", len(accounts), dup, note=f"duplicate of {src['account_id']}")
        dup_map.append((dup["account_id"], src["account_id"]))
        next_id += 1
    report["crm_duplicates"] = dup_map

    orders = t["shop_orders"].rows
    max_no = max(int(o["order_id"][3:]) for o in orders)
    picks = _pick(rng, len(orders), round(q4["shop_duplicate_order_rate"] * len(orders)))
    lo, hi = q4["duplicate_order_delay_minutes"]
    for k, pos in enumerate(reversed(picks)):          # insert from the end so earlier positions hold
        o = copy.deepcopy(orders[pos])
        placed = dt.datetime.strptime(o["order_date"], TIME_FMT) + dt.timedelta(minutes=int(rng.integers(lo, hi + 1)))
        o["order_id"] = f"SO-{max_no + len(picks) - k}"
        o["order_date"] = placed.strftime(TIME_FMT)
        mess.add_row("QI-04", "shop_orders", pos + 1, o, note=f"double submission of {orders[pos]['order_id']}")

    bills = t["telecoms_bills"].rows
    gone = set(qi11_removed_accounts(cfg, context, seed))
    cand = [i for i, b in enumerate(bills) if b["account_no"] not in gone]
    picks = [cand[i] for i in _pick(rng, len(cand), round(q4["telecoms_duplicate_bill_rate"] * len(bills)))]
    for pos in reversed(picks):
        mess.add_row("QI-04", "telecoms_bills", pos + 1, dict(bills[pos]),
                     note=f"bill export re-run: duplicate of {bills[pos]['bill_id']}")

    # ---- QI-05 invalid values ------------------------------------------------------------------
    rng = named_stream(seed, "mess.QI-05")
    q5 = qi["QI-05_invalid_values"]
    closed_pos = [i for i, r in enumerate(hd) if r["closed_at"] and "/" not in r["opened_at"]]
    for i in _pick(rng, len(closed_pos), round(q5["ticket_closed_before_opened_rate"] * len(hd))):
        pos = closed_pos[i]
        opened = dt.datetime.strptime(hd[pos]["opened_at"], TIME_FMT)
        bad = opened - dt.timedelta(minutes=int(rng.integers(30, 60 * 48)))
        mess.change("QI-05", "helpdesk", pos, "closed_at", bad.strftime(TIME_FMT), note="closed before opened")
    for pos in _pick(rng, len(hd), round(q5["ticket_invalid_priority_rate"] * len(hd))):
        mess.change("QI-05", "helpdesk", pos, "priority", str(rng.choice(q5["invalid_priority_values"])),
                    note="invalid priority")
    orders = t["shop_orders"].rows
    for pos in _pick(rng, len(orders), round(q5["order_nonpositive_quantity_rate"] * len(orders))):
        o = orders[pos]
        new_lines = copy.deepcopy(o["lines"])
        j = int(rng.integers(len(new_lines)))
        new_lines[j]["quantity"] = int(rng.choice(q5["nonpositive_quantities"]))
        mess.change("QI-05", "shop_orders", pos, "lines", new_lines,
                    note=f"line {j + 1} quantity set to {new_lines[j]['quantity']}; totals left as recorded")
    lines_tbl = t["crm_lines"].rows
    for pos in _pick(rng, len(lines_tbl), round(q5["crm_contract_value_nonstandard_rate"] * len(lines_tbl))):
        v = lines_tbl[pos]["annual_value"]
        style = int(rng.integers(5))
        new = [f"£{v:,}", f"{v:.2f}", f"{v / 1000:g}k", f"£{v / 1000:g}k", f"{v:,} GBP"][style]
        mess.change("QI-05", "crm_lines", pos, "annual_value", new, note="non-standard contract value")

    # ---- QI-06 category variants ------------------------------------------------------------------
    rng = named_stream(seed, "mess.QI-06")
    variants = qi["QI-06_category_variants"]
    for pos in _pick(rng, len(hd), round(qi["QI-06_category_variants_rate"] * len(hd))):
        cat = hd[pos]["category"]
        mess.change("QI-06", "helpdesk", pos, "category", str(rng.choice(variants[cat])))

    return report


def qi11_removed_accounts(cfg: dict[str, Any], context: dict, seed: int) -> list[str]:
    """Telecoms accounts that QI-11 removes (BL2 line sold, no telecoms account).

    Decided up front from the clean data on its own stream, so QI-04 and QI-10 can avoid
    these accounts and their counts stay exact. Never an at-risk customer (their bills carry
    a planted fall), a look-alike, the account-switch trap, or a closed customer."""
    rng = named_stream(seed, "mess.QI-11.remove")
    customers = context["customers"].set_index("account_id")
    switch = set(context["shop_truth"].loc[context["shop_truth"].switches_to_second, "account_id"])
    protected = set(customers.index[customers.at_risk | (customers.lookalike_group != "")]) | switch
    acc_of = context["telecoms_truth"].set_index("account_no").account_id
    eligible = sorted(a for a, acc in acc_of.items() if acc not in protected and customers.status[acc] == "active")
    k = cfg["quality_issues"]["QI-11_crm_telecoms_mismatch"]["bl2_line_without_telecoms_account"]
    return sorted(str(x) for x in rng.choice(eligible, size=k, replace=False))


# --- Part B: QI-07 to QI-11 ----------------------------------------------------------------------

def _slug(name: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", core_name(name).replace("&", "and")).strip("-")


def apply_part_b(cfg: dict[str, Any], mess: Mess, context: dict, seed: int | None = None) -> dict:
    from faker import Faker
    from generator.telecoms import billing_style
    seed = cfg["seed"] if seed is None else seed
    qi = cfg["quality_issues"]
    t = mess.tables
    customers = context["customers"]
    names = customers.set_index("account_id").company_name
    at_risk = set(customers.loc[customers.at_risk, "account_id"])
    report: dict[str, Any] = {}
    hd = t["helpdesk"].rows
    tickets_truth = context["tickets_truth"].set_index("ticket_id").account_id

    # ---- QI-07 personal data in ticket text -------------------------------------------------
    rng = named_stream(seed, "mess.QI-07")
    fake = Faker("en_GB")
    fake.seed_instance(int(rng.integers(2**31)))
    staff = {p.split()[-1].lower() for p in cfg["tickets"]["agents"] + cfg["customers"]["account_managers"]}
    prefixes = qi["QI-07_phone_prefixes"]

    def contact_name() -> str:
        while True:
            first, last = fake.first_name(), fake.last_name()
            if last.lower() not in staff and first.lower() not in staff:
                return f"{first} {last}"

    def phone() -> str:
        return f"{prefixes[int(rng.integers(len(prefixes)))]}{int(rng.integers(1000)):03d}"

    pii_counts = {"name": 0, "email": 0, "phone": 0}
    for pos in _pick(rng, len(hd), round(qi["QI-07_pii_in_ticket_text_rate"] * len(hd))):
        acc = tickets_truth[hd[pos]["ticket_id"]]
        person = contact_name()
        first, last = person.split(" ", 1)
        email = f"{first.lower()}.{last.lower().replace(' ', '')}@{_slug(names[acc])}.example"
        num = phone()
        form = int(rng.integers(4))
        if form == 0:
            sentence, spans = f" Spoke to {person}.", [(person, "name")]
        elif form == 1:
            sentence, spans = f" Contact {person} on {num}.", [(person, "name"), (num, "phone")]
        elif form == 2:
            sentence, spans = f" Please email {person} at {email} when fixed.", [(person, "name"), (email, "email")]
        else:
            sentence, spans = f" Call back on {num}.", [(num, "phone")]
        mess.change("QI-07", "helpdesk", pos, "description", hd[pos]["description"] + sentence,
                    note="personal data added to description")
        for text, kind in spans:
            mess.annotate("QI-07", "helpdesk", pos, "description", text, f"pii:{kind}")
            pii_counts[kind] += 1
    report["pii_counts"] = pii_counts

    # ---- QI-08 missing values ------------------------------------------------------------------
    rng = named_stream(seed, "mess.QI-08")
    q8 = qi["QI-08_missing_values"]
    lines_tbl = t["crm_lines"].rows
    cand = [i for i, r in enumerate(lines_tbl) if r["renewal_date"] is not None]
    for i in _pick(rng, len(cand), round(q8["crm_renewal_date_rate"] * len(cand))):
        mess.change("QI-08", "crm_lines", cand[i], "renewal_date", None, note="renewal date missing")
    orders = t["shop_orders"].rows
    cand = [i for i, o in enumerate(orders) if o["delivered_date"] is not None]
    n_clean = sum(1 for o in context["payload"]["orders"] if o["delivered_date"] is not None)
    for i in _pick(rng, len(cand), round(q8["shop_delivered_date_rate"] * n_clean)):
        mess.change("QI-08", "shop_orders", cand[i], "delivered_date", None, note="delivered date missing")
    cand = [i for i, r in enumerate(hd) if r["first_responded_at"]]
    lost_closed = 0
    for i in _pick(rng, len(cand), round(q8["ticket_first_responded_at_rate"] * len(cand))):
        lost_closed += bool(hd[cand[i]]["closed_at"])
        mess.change("QI-08", "helpdesk", cand[i], "first_responded_at", "", note="first response time missing")
    report["closed_tickets_losing_first_response"] = lost_closed
    report["closed_tickets"] = sum(1 for r in hd if r["closed_at"])
    cand = [i for i, r in enumerate(hd) if r["kb_article_id"]]
    for i in _pick(rng, len(cand), round(q8["ticket_kb_link_missing_when_used_rate"] * len(cand))):
        mess.change("QI-08", "helpdesk", cand[i], "kb_article_id", "", note="KB link not recorded")

    # ---- QI-10 cancelled and refunded orders, credit notes ------------------------------------------
    rng = named_stream(seed, "mess.QI-10")
    q10 = qi["QI-10_negative_or_excluded_amounts"]
    orders = t["shop_orders"].rows
    n_orders_clean = len(context["payload"]["orders"])
    next_no = max(int(o["order_id"][3:]) for o in orders) + 1
    as_of = cfg["as_of_date"]
    extra = [("cancelled", round(q10["order_cancelled_rate"] * n_orders_clean)),
             ("refunded", round(q10["order_refunded_rate"] * n_orders_clean))]
    n_extra = sum(k for _, k in extra)
    sources = _pick(rng, len(orders), n_extra)                 # distinct source orders
    statuses = ["cancelled"] * extra[0][1] + ["refunded"] * extra[1][1]
    statuses = [statuses[int(i)] for i in rng.permutation(n_extra)]
    for offset, (src, status) in enumerate(zip(sources, statuses)):
        pos = src + offset + 1                      # each earlier insert shifts this row by one
        o = copy.deepcopy(orders[src + offset])
        placed = dt.datetime.strptime(o["order_date"], TIME_FMT) + dt.timedelta(hours=int(rng.integers(1, 48)))
        if placed.date() > as_of:
            placed = dt.datetime.combine(as_of, dt.time(17, 0))
        o.update(order_id=f"SO-{next_no}", order_date=placed.strftime(TIME_FMT), status=status)
        if status == "cancelled":
            o["delivered_date"] = None
        next_no += 1
        mess.add_row("QI-10", "shop_orders", pos, o, note=f"{status} order (exclude from revenue)")
    report["orders_cancelled"], report["orders_refunded"] = extra[0][1], extra[1][1]

    bills = t["telecoms_bills"].rows
    lo, hi = q10["credit_note_share_of_rental"]
    seen = set()
    gone = set(qi11_removed_accounts(cfg, context, seed))
    cand = [i for i, b in enumerate(bills) if b["account_no"] not in gone
            and not ((b["bill_id"], b["bill_month"]) in seen or seen.add((b["bill_id"], b["bill_month"])))]
    picks = [cand[i] for i in _pick(rng, len(cand), round(q10["telecoms_credit_note_rate"] * len(context["telecoms_bills_clean"])))]
    credits = []
    for k, pos in enumerate(sorted(picks, reverse=True)):
        b = bills[pos]
        amount = round(b["line_rental"] * float(rng.uniform(lo, hi)), 2)
        cn = dict(b, bill_id=f"CN-{len(picks) - k:06d}", line_rental=-amount, call_charges=0.0, total=-amount)
        mess.add_row("QI-10", "telecoms_bills", pos + 1, cn, note=f"credit note against {b['bill_id']}")
        credits.append((b["account_no"], amount))
    report["credit_notes"] = credits

    # ---- QI-11 CRM / telecoms mismatches -----------------------------------------------------------------
    rng = named_stream(seed, "mess.QI-11")
    q11 = qi["QI-11_crm_telecoms_mismatch"]
    tel_truth = context["telecoms_truth"]
    acc_of = tel_truth.set_index("account_no").account_id
    switch = set(context["shop_truth"].loc[context["shop_truth"].switches_to_second, "account_id"])
    protected = at_risk | switch | set(customers.loc[customers.lookalike_group != "", "account_id"])
    contracts = t["telecoms_contracts"].rows
    removed = qi11_removed_accounts(cfg, context, seed)
    removed_value = 0.0
    for account_no in sorted(removed):
        for pos in reversed([i for i, b in enumerate(t["telecoms_bills"].rows) if b["account_no"] == account_no]):
            removed_value += t["telecoms_bills"].rows[pos]["total"]
            mess.remove_row("QI-11", "telecoms_bills", pos, note=f"BL2 line sold but no telecoms account ({account_no})")
        pos = next(i for i, r in enumerate(contracts) if r["account_no"] == account_no)
        mess.remove_row("QI-11", "telecoms_contracts", pos, note=f"BL2 line sold but no telecoms account ({account_no})")
    report["qi11_removed"] = [(a, acc_of[a]) for a in sorted(removed)]
    report["qi11_removed_value"] = round(removed_value, 2)

    bl2_customers = set(context["lines"].loc[context["lines"].business_line == "BL2", "account_id"])
    pool = sorted(customers.loc[(customers.status == "active") & (customers.customer_since < cfg["window"]["start"]), "account_id"])
    pool = [a for a in pool if a not in bl2_customers and a not in protected]
    added_accounts = [str(x) for x in rng.choice(pool, size=q11["telecoms_account_without_bl2_line"], replace=False)]
    tel = cfg["telecoms"]
    lo_v, hi_v = cfg["contract_values"]["annual_value_range"]["BL2"]
    s_lo, s_hi = q11["unlisted_account_start_range"]
    next_tel = max(int(r["account_no"][4:]) for r in context["telecoms_contracts_clean"]) + 1
    next_tc = len(context["telecoms_contracts_clean"]) + 1
    added_value = 0.0
    new_truth = []
    next_bill = max(int(b["bill_id"][5:]) for b in context["telecoms_bills_clean"])
    for acc in added_accounts:
        annual = int(round(rng.uniform(lo_v, hi_v), -2))
        rental = round(annual * (1 - tel["call_charge_share"]) / 12, 2)
        start = s_lo + dt.timedelta(days=int(rng.integers((s_hi - s_lo).days + 1)))
        account_no, ref = f"TEL-{next_tel}", f"TC-{next_tc:05d}"
        row = {"contract_ref": ref, "account_no": account_no, "account_name": billing_style(names[acc]),
               "service_type": str(rng.choice(tel["service_types"])), "monthly_charge": rental,
               "start_date": start, "end_date": dt.date(start.year + 2, start.month, min(start.day, 28))}
        while row["end_date"] <= as_of:
            row["end_date"] = dt.date(row["end_date"].year + 2, row["end_date"].month, row["end_date"].day)
        mess.add_row("QI-11", "telecoms_contracts", len(contracts), row,
                     note=f"telecoms account with no BL2 line in the CRM ({acc})")
        new_truth.append({"account_no": account_no, "account_id": acc, "line_id": ""})
        month = dt.date(max(start, cfg["window"]["start"]).year, max(start, cfg["window"]["start"]).month, 1)
        mean_calls = annual * tel["call_charge_share"] / 12
        bills = t["telecoms_bills"].rows
        while month <= as_of:
            nxt = dt.date(month.year + (month.month == 12), month.month % 12 + 1, 1)
            calls = round(float(rng.gamma(tel["call_charge_shape"], mean_calls / tel["call_charge_shape"])), 2)
            first_month = start >= month and start < nxt
            frac = ((nxt - start).days / (nxt - month).days) if first_month else 1.0
            next_bill += 1
            b = {"bill_id": f"BILL-{next_bill:06d}", "account_no": account_no,
                 "bill_month": month.strftime("%Y-%m"), "bill_date": nxt - dt.timedelta(days=1),
                 "line_rental": round(rental * frac, 2), "call_charges": round(calls * frac, 2)}
            b["total"] = round(b["line_rental"] + b["call_charges"], 2)
            added_value += b["total"]
            mess.add_row("QI-11", "telecoms_bills", len(bills), b,
                         note=f"bill for telecoms account with no BL2 line ({acc})")
            month = nxt
        next_tel += 1
        next_tc += 1
    report["qi11_added"] = [(r["account_no"], r["account_id"]) for r in new_truth]
    report["qi11_added_value"] = round(added_value, 2)
    context["telecoms_truth"] = pd.concat([tel_truth, pd.DataFrame(new_truth)], ignore_index=True)

    lo_d, hi_d = q11["end_date_shift_days"]
    remaining = [i for i, r in enumerate(contracts) if r["account_no"] in set(acc_of.index) - set(removed)
                 and r["end_date"] is not None and acc_of[r["account_no"]] not in protected
                 and customers.set_index("account_id").status[acc_of[r["account_no"]]] == "active"]
    shifted = []
    for i in _pick(rng, len(remaining), q11["end_date_mismatch_over_30_days"]):
        pos = remaining[i]
        days = int(rng.integers(lo_d, hi_d + 1)) * (1 if rng.random() < 0.5 else -1)
        new_end = contracts[pos]["end_date"] + dt.timedelta(days=days)
        mess.change("QI-11", "telecoms_contracts", pos, "end_date", new_end, note=f"end date differs from CRM by {days} days")
        shifted.append((contracts[pos]["account_no"], days))
    report["qi11_shifted"] = shifted
    return report


# --- reporting ----------------------------------------------------------------------------------

def record_account(known: pd.DataFrame, context: dict) -> pd.Series:
    """True ACC- ID behind each logged record (for the overlap report)."""
    tickets = context["tickets_truth"].set_index("ticket_id").account_id
    tel = context["telecoms_truth"].set_index("account_no").account_id
    shop = context["shop_truth"].set_index("shop_customer_id").account_id
    lines = context["lines"].set_index("line_id").account_id
    orders = {o["order_id"]: shop[o["customer_id"]] for o in context["payload"]["orders"]}
    contracts = {ref: tel[no] for ref, no in context["contract_account"].items()}
    added = known[(known.table == "telecoms_contracts") & (known.op == "add_row")]
    for e in added.itertuples():
        row = decode(e.new_json)
        contracts[row["contract_ref"]] = tel.get(row["account_no"], "")

    def acc(e) -> str:
        k = e.record_key
        if e.table == "crm_accounts":
            note = e.note or ""
            return note.split()[-1] if note.startswith("duplicate of") else k
        if e.table == "crm_lines":
            return lines.get(k, "")
        if e.table == "helpdesk":
            return tickets.get(k, "")
        if e.table == "shop_orders":
            if k in orders:
                return orders[k]
            return orders.get((e.note or "").split()[-1], "")
        if e.table == "telecoms_contracts":
            return contracts.get(k, "")
        if e.table == "telecoms_bills":
            row = decode(e.new_json if e.op == "add_row" else e.original_json)
            if isinstance(row, dict):
                return tel.get(row["account_no"], "")
            return ""
        return ""
    return known.apply(acc, axis=1)


def counts_table(cfg, known: pd.DataFrame, tables_clean: dict[str, Table]) -> pd.DataFrame:
    qi = cfg["quality_issues"]
    n_hd = len(tables_clean["helpdesk"].rows)
    n_orders = len(tables_clean["shop_orders"].rows)
    n_bills = len(tables_clean["telecoms_bills"].rows)
    n_lines = len(tables_clean["crm_lines"].rows)
    n_crm_dates = sum(1 for tbl, flds in (("crm_accounts", ["customer_since", "closed_date"]),
                                          ("crm_lines", ["contract_start", "renewal_date", "end_date"]))
                      for r in tables_clean[tbl].rows for f in flds if r[f] is not None)
    k = known

    def c(q, table=None, fld=None, op=None, note=None):
        m = k.qi == q
        if table:
            m &= k.table == table
        if fld:
            m &= k.field == fld
        if op:
            m &= k.op == op
        if note:
            m &= k.note.str.contains(note, regex=False)
        return int(m.sum())

    shop_accounts = k[(k.qi == "QI-01") & (k.table == "shop_orders")].note.nunique()
    rows = [
        ("QI-01", "helpdesk ticket names", f"{qi['QI-01_name_variants']['helpdesk']:.0%} of {n_hd}",
         round(qi["QI-01_name_variants"]["helpdesk"] * n_hd), c("QI-01", "helpdesk")),
        ("QI-01", "shop account names", f"{qi['QI-01_name_variants']['shop']:.0%} of shop accounts",
         round(qi["QI-01_name_variants"]["shop"] * len(set(o["customer_id"] for o in tables_clean["shop_orders"].rows))),
         shop_accounts),
        ("QI-01", "telecoms extra variants", f"{qi['QI-01_name_variants']['telecoms_extra']:.0%} of contracts",
         round(qi["QI-01_name_variants"]["telecoms_extra"] * len(tables_clean["telecoms_contracts"].rows)),
         c("QI-01", "telecoms_contracts")),
        ("QI-02", "no shared customer ID", "structural", "-", "structural"),
        ("QI-03", "CRM dates non-ISO", f"{qi['QI-03_mixed_dates']['crm']:.0%} of {n_crm_dates} date cells",
         round(qi["QI-03_mixed_dates"]["crm"] * n_crm_dates), c("QI-03", "crm_accounts") + c("QI-03", "crm_lines")),
        ("QI-03", "bill months non-ISO", f"{qi['QI-03_mixed_dates']['telecoms_bill_month']:.0%} of {n_bills}",
         round(qi["QI-03_mixed_dates"]["telecoms_bill_month"] * n_bills), c("QI-03", "telecoms_bills")),
        ("QI-03", "tickets with non-ISO times", f"{qi['QI-03_mixed_dates']['helpdesk']:.0%} of {n_hd}",
         round(qi["QI-03_mixed_dates"]["helpdesk"] * n_hd), k[(k.qi == "QI-03") & (k.table == "helpdesk")].record_key.nunique()),
        ("QI-04", "CRM duplicate accounts", "count", qi["QI-04_duplicates"]["crm_duplicate_accounts"],
         c("QI-04", "crm_accounts", op="add_row")),
        ("QI-04", "second shop accounts", "count (structural, from step 1.7)",
         qi["QI-04_duplicates"]["shop_duplicate_accounts"], cfg["shop"]["second_accounts"]),
        ("QI-04", "duplicate orders", f"{qi['QI-04_duplicates']['shop_duplicate_order_rate']:.1%} of {n_orders}",
         round(qi["QI-04_duplicates"]["shop_duplicate_order_rate"] * n_orders), c("QI-04", "shop_orders", op="add_row")),
        ("QI-04", "duplicate bill rows", f"{qi['QI-04_duplicates']['telecoms_duplicate_bill_rate']:.1%} of {n_bills}",
         round(qi["QI-04_duplicates"]["telecoms_duplicate_bill_rate"] * n_bills), c("QI-04", "telecoms_bills", op="add_row")),
        ("QI-05", "closed before opened", f"{qi['QI-05_invalid_values']['ticket_closed_before_opened_rate']:.1%} of {n_hd}",
         round(qi["QI-05_invalid_values"]["ticket_closed_before_opened_rate"] * n_hd), c("QI-05", "helpdesk", "closed_at")),
        ("QI-05", "invalid priority", f"{qi['QI-05_invalid_values']['ticket_invalid_priority_rate']:.1%} of {n_hd}",
         round(qi["QI-05_invalid_values"]["ticket_invalid_priority_rate"] * n_hd), c("QI-05", "helpdesk", "priority")),
        ("QI-05", "zero/negative quantity", f"{qi['QI-05_invalid_values']['order_nonpositive_quantity_rate']:.1%} of {n_orders}",
         round(qi["QI-05_invalid_values"]["order_nonpositive_quantity_rate"] * n_orders), c("QI-05", "shop_orders")),
        ("QI-05", "non-standard contract values", f"{qi['QI-05_invalid_values']['crm_contract_value_nonstandard_rate']:.0%} of {n_lines}",
         round(qi["QI-05_invalid_values"]["crm_contract_value_nonstandard_rate"] * n_lines), c("QI-05", "crm_lines")),
        ("QI-06", "category variants", f"{qi['QI-06_category_variants_rate']:.0%} of {n_hd}",
         round(qi["QI-06_category_variants_rate"] * n_hd), c("QI-06", "helpdesk")),
    ]
    n_lines_with_renewal = sum(1 for r in tables_clean["crm_lines"].rows if r["renewal_date"] is not None)
    n_delivered = sum(1 for o in tables_clean["shop_orders"].rows if o["delivered_date"] is not None)
    n_responded = sum(1 for r in tables_clean["helpdesk"].rows if r["first_responded_at"])
    n_linked = sum(1 for r in tables_clean["helpdesk"].rows if r["kb_article_id"])
    q8, q10, q11 = qi["QI-08_missing_values"], qi["QI-10_negative_or_excluded_amounts"], qi["QI-11_crm_telecoms_mismatch"]
    rows += [
        ("QI-07", "tickets with personal data", f"{qi['QI-07_pii_in_ticket_text_rate']:.0%} of {n_hd}",
         round(qi["QI-07_pii_in_ticket_text_rate"] * n_hd), c("QI-07", "helpdesk", op="change")),
        ("QI-07", "personal-data spans recorded", "name / email / phone", "-", c("QI-07", op="pii_span")),
        ("QI-08", "renewal dates removed", f"{q8['crm_renewal_date_rate']:.0%} of {n_lines_with_renewal}",
         round(q8["crm_renewal_date_rate"] * n_lines_with_renewal), c("QI-08", "crm_lines")),
        ("QI-08", "delivered dates removed", f"{q8['shop_delivered_date_rate']:.0%} of {n_delivered}",
         round(q8["shop_delivered_date_rate"] * n_delivered), c("QI-08", "shop_orders")),
        ("QI-08", "first-response times removed", f"{q8['ticket_first_responded_at_rate']:.0%} of {n_responded}",
         round(q8["ticket_first_responded_at_rate"] * n_responded), c("QI-08", "helpdesk", "first_responded_at")),
        ("QI-08", "KB links removed", f"{q8['ticket_kb_link_missing_when_used_rate']:.0%} of {n_linked}",
         round(q8["ticket_kb_link_missing_when_used_rate"] * n_linked), c("QI-08", "helpdesk", "kb_article_id")),
        ("QI-09", "links to superseded article", "count (step 1.8)", qi["QI-09_tickets_linking_superseded_article"],
         qi["QI-09_tickets_linking_superseded_article"]),
        ("QI-10", "cancelled orders added", f"{q10['order_cancelled_rate']:.0%} of {n_orders}",
         round(q10["order_cancelled_rate"] * n_orders), c("QI-10", "shop_orders", note="cancelled")),
        ("QI-10", "refunded orders added", f"{q10['order_refunded_rate']:.0%} of {n_orders}",
         round(q10["order_refunded_rate"] * n_orders), c("QI-10", "shop_orders", note="refunded")),
        ("QI-10", "credit notes added", f"{q10['telecoms_credit_note_rate']:.0%} of {n_bills}",
         round(q10["telecoms_credit_note_rate"] * n_bills), c("QI-10", "telecoms_bills")),
        ("QI-11", "BL2 lines with no telecoms account", "count", q11["bl2_line_without_telecoms_account"],
         c("QI-11", "telecoms_contracts", op="remove_row")),
        ("QI-11", "telecoms accounts with no BL2 line", "count", q11["telecoms_account_without_bl2_line"],
         c("QI-11", "telecoms_contracts", op="add_row")),
        ("QI-11", "end dates moved > 30 days", "count", q11["end_date_mismatch_over_30_days"],
         c("QI-11", "telecoms_contracts", "end_date")),
    ]
    return pd.DataFrame(rows, columns=["qi", "what", "config", "target", "actual"])


def revenue_check(clean: dict[str, Table], messy: dict[str, Table], known: pd.DataFrame) -> pd.DataFrame:
    """BL2 and BL5 revenue: clean truth, messy data summed as it comes, and correctly cleaned."""
    cb = pd.DataFrame(clean["telecoms_bills"].rows)
    mb = pd.DataFrame(messy["telecoms_bills"].rows)
    co = pd.DataFrame(clean["shop_orders"].rows)
    mo = pd.DataFrame(messy["shop_orders"].rows)
    dup_orders = set(known[(known.qi == "QI-04") & (known.table == "shop_orders")].record_key)
    rows = [
        ("BL2", "clean (before any mess)", cb.total.sum()),
        ("BL2", "messy, summed as exported", mb.total.sum()),
        ("BL2", "  of which duplicate bill rows", mb.total.sum() - mb.drop_duplicates().total.sum()),
        ("BL2", "  of which credit notes", mb[mb.bill_id.str.startswith("CN-")].total.sum()),
        ("BL2", "messy, correctly cleaned (duplicates removed, credits applied)", mb.drop_duplicates().total.sum()),
        ("BL5", "clean (before any mess)", co.order_total.sum()),
        ("BL5", "messy, summed as exported", mo.order_total.sum()),
        ("BL5", "  of which cancelled", mo[mo.status == "cancelled"].order_total.sum()),
        ("BL5", "  of which refunded", mo[mo.status == "refunded"].order_total.sum()),
        ("BL5", "  of which duplicate orders", mo[mo.order_id.isin(dup_orders)].order_total.sum()),
        ("BL5", "messy, correctly cleaned",
         mo[~mo.order_id.isin(dup_orders) & ~mo.status.isin(["cancelled", "refunded"])].order_total.sum()),
    ]
    return pd.DataFrame(rows, columns=["line", "view", "revenue"])


def build(cfg, raw_dir: Path = RAW_DIR, truth_dir: Path = GROUND_TRUTH_DIR, seed: int | None = None,
          parts: tuple[str, ...] = ("A", "B")):
    tables, context = clean_tables(cfg, seed)
    clean = copy.deepcopy(tables)
    mess = Mess(tables)
    report = apply_part_a(cfg, mess, context, seed) if "A" in parts else {}
    if "B" in parts:
        report.update(apply_part_b(cfg, mess, context, seed))
    known = mess.known_issues()
    write_tables(mess.tables, context, raw_dir)
    write_ground_truth(cfg, truth_dir, seed=seed)
    _write_csv(context["tickets_truth"], truth_dir / "tickets.csv")
    _write_csv(context["telecoms_truth"], truth_dir / "telecoms_accounts.csv")
    _write_csv(context["shop_truth"], truth_dir / "shop_customers.csv")
    acc_map = pd.DataFrame([(a, a) for a in context["customers"].account_id] + list(report.get("crm_duplicates", [])),
                           columns=["raw_account_id", "account_id"])
    _write_csv(acc_map, truth_dir / "crm_account_map.csv")
    _write_csv(known, truth_dir / "known_issues.csv")
    return clean, mess, known, context, report


def main() -> None:
    cfg = load_config()
    clean, mess, known, context, report = build(cfg)
    print(f"Logged {len(known)} changes in known_issues.csv")
    print("\nActual counts against the config:")
    print(counts_table(cfg, known, clean).to_string(index=False))
    print(f"\nCRM non-ISO dates: {report['crm_non_iso_dates']} cells; ambiguous (day <= 12): "
          f"{report['crm_ambiguous_share']:.0%} "
          "(natural rate; not set in the config)")
    print(f"CRM duplicates (raw ID -> real account): {report['crm_duplicates']}")
    print(f"Personal-data spans inserted (QI-07): {report['pii_counts']}")
    print(f"Closed tickets losing first_responded_at (QI-08): {report['closed_tickets_losing_first_response']} of "
          f"{report['closed_tickets']}; response SLA measurable on "
          f"{report['closed_tickets'] - report['closed_tickets_losing_first_response']}")
    print(f"QI-11 removed accounts (BL2 line, no telecoms account): {report['qi11_removed']} "
          f"(bills removed £{report['qi11_removed_value']:,.2f})")
    print(f"QI-11 added accounts (telecoms account, no BL2 line): {report['qi11_added']} "
          f"(bills added £{report['qi11_added_value']:,.2f})")
    print(f"QI-11 end dates moved: {report['qi11_shifted']}")
    print(f"QI-10 credit notes: {len(report['credit_notes'])}, total £{sum(a for _, a in report['credit_notes']):,.2f}")
    print("\nRevenue (18 months):")
    rc = revenue_check(clean, mess.tables, known)
    print(rc.assign(revenue=rc.revenue.map("£{:,.2f}".format)).to_string(index=False))
    print(f"Name variants rejected because they matched another customer's name: "
          f"{len(report['collisions_rejected'])} {report['collisions_rejected']}")

    customers = context["customers"]
    risk = set(customers.loc[customers.at_risk, "account_id"])
    look = customers[customers.lookalike_group != ""].set_index("account_id").lookalike_group
    switch = set(context["shop_truth"].loc[context["shop_truth"].switches_to_second, "account_id"])
    known = known.assign(account_id=record_account(known, context))
    from generator.patterns import compare
    pat = compare(cfg, clean, mess.tables, known, context)
    print("\nBQ-06 pattern (ticket rise AND a spend fall), active customers flagged:")
    for mode in ("clean", "cleaned", "naive"):
        print(f"  {mode:8s}: {len(pat['flagged'][mode])} {pat['flagged'][mode]}")
    print("At-risk customers, Jul-Sep / Apr-Jun ratios before (naive) and after (cleaned) cleaning:")
    print(pat["at_risk_table"].to_string())
    fl = pat["flags"]
    healthy = set(customers.loc[~customers.at_risk & (customers.status == "active"), "account_id"])
    for mode in ("cleaned", "naive"):
        f = fl[mode]
        h = f[f.index.isin(healthy)]
        print(f"  {mode}: healthy customers with a ticket rise {int(h.ticket_rise.sum())}, BL2 fall "
              f"{int(h.bl2_fall.sum())}, shop fall {int(h.shop_fall.sum())}")
    for acc in sorted(switch):
        print(f"  account-switch trap {acc}: naive shop fall {bool(fl['naive'].shop_fall.get(acc, False))}, "
              f"cleaned shop fall {bool(fl['cleaned'].shop_fall.get(acc, False))}")
    print(f"  naive shop falls on accounts not in the clean customer list (CRM duplicates): "
          f"{sorted(set(fl['naive'].index[fl['naive'].shop_fall]) - set(customers.account_id))}")

    # duplicate orders on at-risk customers: does removing them change anything?
    dups = known[(known.qi == "QI-04") & (known.table == "shop_orders")]
    shop_acc = context["shop_truth"].set_index("shop_customer_id").account_id
    print("Duplicate orders on at-risk customers:")
    for e in dups.itertuples():
        o = decode(e.new_json)
        acc = shop_acc[o["customer_id"]]
        if acc in risk:
            print(f"  {acc}: {o['order_id']} placed {o['order_date']} (£{o['order_total']:.2f}), duplicate of "
                  f"{e.note.split()[-1]}; quarter {'Jul-Sep' if o['order_date'][:7] >= '2026-07' else 'Apr-Jun' if o['order_date'][:7] >= '2026-04' else 'outside both'}")

    print("\nInjected problems touching planted cases:")
    for label, ids in (("at-risk", sorted(risk)), ("look-alike", sorted(look.index)),
                       ("account-switch trap", sorted(switch))):
        print(f"-- {label}")
        for acc in ids:
            sub = known[known.account_id == acc]
            parts = ", ".join(f"{q}/{tb}: {n}" for (q, tb), n in sub.groupby(["qi", "table"]).size().items())
            tag = f" [{look[acc]}]" if acc in look.index else ""
            print(f"   {acc}{tag}: {len(sub)} changes ({parts})")


if __name__ == "__main__":
    main()
