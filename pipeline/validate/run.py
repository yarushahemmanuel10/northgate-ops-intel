"""Validate and type the staging tables.

Run:  python -m pipeline.validate.run   (after pipeline.ingest.run)

Reads data/staging/*.csv; writes data/validated/<table>.csv (every staging row and column,
plus typed columns ending in "__value") and data/validated/validation_findings.csv.
Rows are never dropped and values are never invented: unparseable text stays as it was,
with an empty typed value and a finding (D-018).
"""
from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

import yaml

from pipeline.ingest.common import write_staging
from pipeline.settings import ROOT, load_settings
from pipeline.validate.parse import parse_date, parse_money, parse_number, parse_priority

FINDING_COLUMNS = ["rule_id", "table", "record_key", "source_row", "field", "original", "action", "value", "detail"]


def load_rules(path: Path = ROOT / "config" / "validation.yaml",
               vocabulary_path: Path = ROOT / "config" / "vocabulary.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        rules = yaml.safe_load(f)
    with open(vocabulary_path, encoding="utf-8") as f:
        rules["vocabulary"] = yaml.safe_load(f)
    return rules


def category_lookup(vocabulary: dict) -> dict[str, str]:
    """Case-insensitive lookup from any accepted spelling to its canonical ticket category."""
    voc = vocabulary["ticket_category"]
    canonical = voc["canonical"]
    bad = sorted({v for v in voc["variants"].values() if v not in canonical})
    if bad or len(set(canonical)) != len(canonical):
        raise ValueError(f"vocabulary.yaml: variants must map to a canonical category (bad: {bad})")
    lookup = {c.strip().casefold(): c for c in canonical}
    lookup.update({k.strip().casefold(): v for k, v in voc["variants"].items()})
    return lookup


def _read(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, dt.datetime):
        return v.strftime("%Y-%m-%d %H:%M")
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


class Validator:
    def __init__(self, rules: dict):
        self.r = rules
        lo, hi = rules["dates"]["plausible_range"]
        self.plausible = (lo, hi)
        self.findings: list[dict] = []
        self.categories = category_lookup(rules["vocabulary"])

    def note(self, rule, table, row, key, field, original, action, value, detail):
        self.findings.append({"rule_id": rule, "table": table, "record_key": row[key],
                              "source_row": row["_source_row"], "field": field, "original": original,
                              "action": action, "value": _fmt(value), "detail": detail})

    def date(self, table, row, key, field, formats_key, timestamp=False):
        value, action, detail = parse_date(row[field], self.r["dates"][formats_key], self.plausible, timestamp)
        rule = {"converted": "DATE-001", "converted_by_convention": "DATE-002", "flagged": "DATE-003"}.get(action)
        if rule:
            self.note(rule, table, row, key, field, row[field], action, value, detail)
        row[f"{field}__value"] = _fmt(value)
        return value

    def money(self, table, row, key, field):
        value, action, detail = parse_money(row[field], self.r["money"]["allow_k_suffix"])
        rule = {"converted": "MONEY-001", "flagged": "MONEY-002"}.get(action)
        if rule:
            self.note(rule, table, row, key, field, row[field], action, value, detail)
        row[f"{field}__value"] = _fmt(value)
        return value

    # ---- tables --------------------------------------------------------------------
    def crm_accounts(self, rows):
        for r in rows:
            self.date("crm_accounts", r, "Account ID", "Customer Since", "crm")
            self.date("crm_accounts", r, "Account ID", "Closed Date", "crm")
            if r["Status"] not in self.r["crm"]["statuses"]:
                self.note("CRM-001", "crm_accounts", r, "Account ID", "Status", r["Status"], "flagged", None,
                          "unknown status")

    def crm_lines(self, rows):
        for r in rows:
            self.date("crm_contract_lines", r, "Line ID", "Contract Start", "crm")
            renewal = self.date("crm_contract_lines", r, "Line ID", "Renewal Date", "crm")
            end = self.date("crm_contract_lines", r, "Line ID", "End Date", "crm")
            self.money("crm_contract_lines", r, "Line ID", "Annual Value (GBP)")
            if r["Business Line"] not in self.r["crm"]["business_lines"]:
                self.note("CRM-002", "crm_contract_lines", r, "Line ID", "Business Line", r["Business Line"],
                          "flagged", None, "unknown business line")
            if not r["Renewal Date"].strip() and not r["End Date"].strip():
                self.note("MISSING-003", "crm_contract_lines", r, "Line ID", "Renewal Date", "", "flagged", None,
                          "active contract line with no renewal date")

    def tickets(self, rows):
        p = self.r["priorities"]
        for r in rows:
            opened = self.date("helpdesk_tickets", r, "ticket_id", "opened_at", "helpdesk_timestamp", True)
            responded = self.date("helpdesk_tickets", r, "ticket_id", "first_responded_at", "helpdesk_timestamp", True)
            closed = self.date("helpdesk_tickets", r, "ticket_id", "closed_at", "helpdesk_timestamp", True)
            value, action, detail = parse_priority(r["priority"], p["valid"], p["case_insensitive"], p["mapping"])
            if action != "ok":
                rule = "PRIORITY-001" if action in ("normalised", "converted") else "PRIORITY-002"
                self.note(rule, "helpdesk_tickets", r, "ticket_id", "priority", r["priority"], action, value, detail)
            r["priority__value"] = value or ""
            raw_cat = r["category"]
            cat = self.categories.get(raw_cat.strip().casefold())
            if cat is None:
                self.note("CATEGORY-001", "helpdesk_tickets", r, "ticket_id", "category", raw_cat, "flagged", None,
                          "category not in the controlled vocabulary (config/vocabulary.yaml)")
            elif cat != raw_cat:
                self.note("CATEGORY-002", "helpdesk_tickets", r, "ticket_id", "category", raw_cat, "mapped", cat,
                          "variant mapped to its canonical category")
            r["category__value"] = cat or ""
            if opened and closed and closed < opened:
                self.note("TIME-001", "helpdesk_tickets", r, "ticket_id", "closed_at", r["closed_at"], "flagged",
                          None, "closed before opened")
            if opened and responded and responded < opened:
                self.note("TIME-002", "helpdesk_tickets", r, "ticket_id", "first_responded_at",
                          r["first_responded_at"], "flagged", None, "first response before opened")
            # an open ticket with no response yet is not a finding: it is not due
            measurable = bool(closed and responded)
            if r["closed_at"].strip() and not r["first_responded_at"].strip():
                self.note("MISSING-001", "helpdesk_tickets", r, "ticket_id", "first_responded_at", "", "flagged",
                          None, "closed ticket with no first response: response SLA can't be measured")
            r["response_sla_measurable"] = "true" if measurable else "false"

    def bills(self, rows):
        tol = self.r["arithmetic_tolerance"]
        prefix = self.r["telecoms"]["credit_note_prefix"]
        for r in rows:
            self.date("telecoms_bills", r, "bill_id", "bill_month", "telecoms_bill_month")
            self.date("telecoms_bills", r, "bill_id", "bill_date", "telecoms_date")
            nums = {f: parse_number(r[f])[0] for f in ("line_rental", "call_charges", "total")}
            for f, v in nums.items():
                r[f"{f}__value"] = _fmt(v)
            if None not in nums.values() and abs(nums["line_rental"] + nums["call_charges"] - nums["total"]) > tol:
                self.note("ARITH-003", "telecoms_bills", r, "bill_id", "total", r["total"], "flagged", None,
                          "total is not line rental plus call charges")
            r["revenue_treatment"] = "negative" if r["bill_id"].startswith(prefix) else "include"

    def contracts(self, rows):
        for r in rows:
            self.date("telecoms_contracts", r, "contract_ref", "start_date", "telecoms_date")
            self.date("telecoms_contracts", r, "contract_ref", "end_date", "telecoms_date")

    def orders(self, rows, lines):
        s = self.r["shop"]
        tol = self.r["arithmetic_tolerance"]
        by_order: dict[str, list[dict]] = {}
        for ln in lines:
            by_order.setdefault(ln["order_id"], []).append(ln)
            q, price, total = (parse_number(ln[f])[0] for f in ("quantity", "unit_price", "line_total"))
            ln["quantity__value"], ln["unit_price__value"], ln["line_total__value"] = _fmt(q), _fmt(price), _fmt(total)
            if q is not None and q <= 0:
                self.note("QTY-001", "shop_order_lines", ln, "order_id", "quantity", ln["quantity"], "flagged", None,
                          f"line {ln['line_no']}: quantity must be positive")
            if None not in (q, price, total) and abs(q * price - total) > tol:
                self.note("ARITH-002", "shop_order_lines", ln, "order_id", "line_total", ln["line_total"], "flagged",
                          None, f"line {ln['line_no']}: line total is not quantity x unit price")
        as_of = self.r["as_of_date"]
        for r in rows:
            self.date("shop_orders", r, "order_id", "order_date", "shop_timestamp", True)
            self.date("shop_orders", r, "order_id", "promised_date", "shop_date")
            self.date("shop_orders", r, "order_id", "delivered_date", "shop_date")
            total = parse_number(r["order_total"])[0]
            r["order_total__value"] = _fmt(total)
            expected = sum((parse_number(ln["quantity"])[0] or 0) * (parse_number(ln["unit_price"])[0] or 0)
                           for ln in by_order.get(r["order_id"], []))
            if total is not None and abs(expected - total) > tol:
                self.note("ARITH-001", "shop_orders", r, "order_id", "order_total", r["order_total"], "flagged", None,
                          f"order total {total:.2f} is not the sum of quantity x unit price ({expected:.2f})")
            if r["status"] not in s["statuses"]:
                self.note("SHOP-001", "shop_orders", r, "order_id", "status", r["status"], "flagged", None,
                          "unknown status")
            # undelivered orders are not findings; a 'delivered' order with no date should have one
            if r["status"] == "delivered" and not r["delivered_date"].strip():
                self.note("MISSING-002", "shop_orders", r, "order_id", "delivered_date", "", "flagged", None,
                          "status delivered but no delivered date")
            r["revenue_treatment"] = s["revenue_treatment"].get(r["status"], "review")


def validate(settings: dict, rules: dict, staging_dir: Path | None = None, out_dir: Path | None = None) -> dict:
    staging = staging_dir or settings["staging_dir"]
    out = out_dir or settings["validated_dir"]
    v = Validator(rules)
    tables = {n: _read(staging / f"{n}.csv") for n in
              ("crm_accounts", "crm_contract_lines", "helpdesk_tickets", "telecoms_contracts", "telecoms_bills",
               "shop_orders", "shop_order_lines", "kb_documents")}
    columns = {n: list(rows[0].keys()) if rows else [] for n, rows in tables.items()}
    v.crm_accounts(tables["crm_accounts"])
    v.crm_lines(tables["crm_contract_lines"])
    v.tickets(tables["helpdesk_tickets"])
    v.contracts(tables["telecoms_contracts"])
    v.bills(tables["telecoms_bills"])
    v.orders(tables["shop_orders"], tables["shop_order_lines"])
    counts = {}
    for name, rows in tables.items():
        cols = columns[name] + [c for c in (rows[0].keys() if rows else []) if c not in columns[name]]
        write_staging(rows, cols, out / f"{name}.csv")
        counts[name] = len(rows)
    write_staging(v.findings, FINDING_COLUMNS, out / "validation_findings.csv")
    counts["findings"] = len(v.findings)
    return counts


def main() -> None:
    settings = load_settings()
    counts = validate(settings, load_rules())
    print(f"Validated tables written to {settings['validated_dir']}:")
    for name, n in counts.items():
        print(f"  {name}: {n}")


if __name__ == "__main__":
    main()
