"""Build the Layer 2 star schema in DuckDB (step 3.3, D-025).

Run:  python -m pipeline.warehouse.load   (after pipeline.resolve.run and pipeline.warehouse.dedup)

Reads data/validated/, data/resolved/crosswalk.csv, data/warehouse/duplicates.csv and
config/review_decisions.csv. Writes data/warehouse/northgate.duckdb, rebuilt from scratch on
every run (the previous file is replaced). Nothing from the answer key or the generator is read.

- Every fact links to dim_customer. Records whose customer is queued or unmatched link to the
  "Unknown customer" row, unless config/review_decisions.csv holds a person's decision for them.
- Marked duplicates (D-024) are left out; nothing else is.
- Facts carry typed values. Where validation found a value invalid, the typed value is empty
  and a *_flag column says why; consistency findings keep the value and add a flag.
"""
from __future__ import annotations

import csv
import datetime as dt
import os
import re
from collections import defaultdict
from pathlib import Path

import duckdb
import pandas as pd
import yaml

from pipeline.settings import ROOT, load_settings
from pipeline.warehouse.kpis import create_dashboard_views, create_kpi_views, load_thresholds
from pipeline.warehouse.views import create_at_risk_view, create_views

DECISION_COLUMNS = ["source_system", "source_key", "decided_account_id", "reviewer", "decided_on", "reason"]
# findings whose value can't be used: the typed value must be empty in the warehouse
VALUE_RULES = {"DATE-001", "MONEY-002", "PRIORITY-002", "CATEGORY-001", "QTY-001"}
LINE_IN_DETAIL = re.compile(r"^line (\d+):")


def load_config(path: Path = ROOT / "config" / "warehouse.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_at_risk(path: Path = ROOT / "config" / "at_risk.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _read(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _none(v):
    return None if v is None or v == "" else v


# ---- findings -> flag columns -------------------------------------------------------------

class Findings:
    def __init__(self, rows: list[dict]):
        self.flags: dict[tuple, list[str]] = defaultdict(list)
        self.invalid: set[tuple] = set()
        for f in rows:
            if f["action"] != "flagged":
                continue
            row = f["source_row"]
            if f["table"] == "shop_order_lines":                 # one row per order: line number is in the detail
                m = LINE_IN_DETAIL.match(f["detail"])
                row = f"{f['record_key']}#{m.group(1)}" if m else row
            key = (f["table"], row, f["field"])
            self.flags[key].append(f"{f['rule_id']}: {f['detail']}")
            if f["rule_id"] in VALUE_RULES:
                self.invalid.add(key)

    def flag(self, table, row, field):
        return "; ".join(self.flags.get((table, row, field), [])) or None

    def value(self, table, row, field, typed):
        return None if (table, row, field) in self.invalid else _none(typed)


# ---- customers ------------------------------------------------------------------------------

def read_decisions(path: Path) -> list[dict]:
    """Rows of config/review_decisions.csv that hold a decision. A row whose decided_account_id
    is blank means "can't decide" and is skipped: the record stays in the queue (D-029).
    Extra columns (e.g. evidence copied from the review sheet) are ignored."""
    out = []
    for r in (_read(path) if path.exists() else []):
        if not (r.get("decided_account_id") or "").strip():
            continue
        missing = [c for c in DECISION_COLUMNS if not (r.get(c) or "").strip()]
        if missing:
            raise ValueError(f"review decision {r} is missing {missing}")
        out.append({c: r[c].strip() for c in DECISION_COLUMNS})
    return out


class CustomerLinks:
    """Customer for every source record: crosswalk, then a person's decision for queued records."""

    def __init__(self, crosswalk: list[dict], decisions: list[dict], canonical: set[str], unknown: str,
                 units: dict[tuple, str]):
        self.unknown = unknown
        self.by_record = {(r["source_table"], r["source_row"]): r for r in crosswalk}
        index: dict[tuple, list[tuple]] = defaultdict(list)    # (system, key or unit) -> records
        for k, r in self.by_record.items():
            index[(r["source_system"], r["source_key"])].append(k)
            if k in units:
                index[(r["source_system"], units[k])].append(k)
        # A decision names a record (ticket, order or bill ID) or a whole queue item (spelling,
        # shop account, telecoms account). Whole-item decisions are applied first and per-record
        # decisions after, so a per-record decision always overrides a whole-item one (D-029).
        record_keys = {(r["source_system"], r["source_key"]) for r in self.by_record.values()}
        levels = {"item": {}, "record": {}}
        for i, d in enumerate(decisions):
            if d["decided_account_id"] not in canonical:
                raise ValueError(f"review decision {i + 1}: {d['decided_account_id']} is not a canonical customer")
            key = (d["source_system"], d["source_key"])
            records = index.get(key)
            if not records:
                raise ValueError(f"review decision {i + 1}: no {d['source_system']} record or queue unit "
                                 f"{d['source_key']!r}")
            level = "record" if key in record_keys else "item"
            for k in records:
                if self.by_record[k]["status"] != "review":
                    raise ValueError(f"review decision {i + 1}: {k} is not in the review queue")
                earlier = levels[level].get(k)
                if earlier and earlier["decided_account_id"] != d["decided_account_id"]:
                    raise ValueError(f"review decision {i + 1}: conflicts with another {level} decision for {k}")
                levels[level][k] = d
        self.decided = {**levels["item"], **levels["record"]}
        self.decision_level = {k: ("record" if k in levels["record"] else "item") for k in self.decided}

    def link(self, table: str, row: str) -> tuple[str, str, str]:
        """(customer_id, match_status, match_method)"""
        r = self.by_record[(table, row)]
        if r["status"] == "auto":
            return r["resolved_account_id"], "auto", r["method"]
        if (table, row) in self.decided:
            return self.decided[(table, row)]["decided_account_id"], "human_review", "human_review"
        return self.unknown, r["status"], r["method"]


# ---- tables -----------------------------------------------------------------------------------

SCHEMAS = {
    "dim_customer": {"customer_id": "VARCHAR", "company_name": "VARCHAR", "sector": "VARCHAR",
                     "account_manager": "VARCHAR", "customer_since": "DATE", "status": "VARCHAR",
                     "closed_date": "DATE", "merged_crm_records": "VARCHAR", "is_unknown": "BOOLEAN"},
    "dim_business_line": {"business_line": "VARCHAR", "name": "VARCHAR", "revenue_source": "VARCHAR",
                          "modelled": "BOOLEAN"},
    "dim_date": {"date": "DATE", "year": "INTEGER", "quarter": "INTEGER", "month": "INTEGER",
                 "month_name": "VARCHAR", "year_month": "VARCHAR", "day_of_week": "INTEGER", "day_name": "VARCHAR",
                 "is_weekend": "BOOLEAN", "in_data_window": "BOOLEAN", "after_as_of": "BOOLEAN"},
    "fact_ticket": {"ticket_id": "VARCHAR", "source_row": "INTEGER", "customer_id": "VARCHAR",
                    "match_status": "VARCHAR", "match_method": "VARCHAR", "business_line": "VARCHAR",
                    "opened_at": "TIMESTAMP", "opened_date": "DATE", "first_responded_at": "TIMESTAMP",
                    "closed_at": "TIMESTAMP", "closed_date": "DATE", "priority": "VARCHAR", "category": "VARCHAR",
                    "agent": "VARCHAR", "kb_article_id": "VARCHAR", "response_sla_measurable": "BOOLEAN",
                    "opened_at_flag": "VARCHAR", "first_responded_at_flag": "VARCHAR", "closed_at_flag": "VARCHAR",
                    "priority_flag": "VARCHAR", "category_flag": "VARCHAR", "response_sla_flag": "VARCHAR"},
    "ticket_text": {"ticket_id": "VARCHAR", "description": "VARCHAR", "resolution_notes": "VARCHAR"},
    "fact_order": {"order_id": "VARCHAR", "shop_customer_id": "VARCHAR", "customer_id": "VARCHAR",
                   "match_status": "VARCHAR", "match_method": "VARCHAR", "business_line": "VARCHAR",
                   "ordered_at": "TIMESTAMP", "order_date": "DATE", "status": "VARCHAR", "promised_date": "DATE",
                   "delivered_date": "DATE", "order_total": "DECIMAL(12,2)", "line_count": "INTEGER",
                   "revenue_treatment": "VARCHAR", "order_date_flag": "VARCHAR", "promised_date_flag": "VARCHAR",
                   "delivered_date_flag": "VARCHAR", "order_total_flag": "VARCHAR"},
    "fact_order_line": {"order_id": "VARCHAR", "line_no": "INTEGER", "customer_id": "VARCHAR",
                        "match_status": "VARCHAR", "business_line": "VARCHAR", "order_date": "DATE",
                        "revenue_treatment": "VARCHAR", "sku": "VARCHAR", "product": "VARCHAR",
                        "quantity": "INTEGER", "unit_price": "DECIMAL(12,2)", "line_total": "DECIMAL(12,2)",
                        "quantity_flag": "VARCHAR", "unit_price_flag": "VARCHAR", "line_total_flag": "VARCHAR"},
    "fact_bill": {"bill_id": "VARCHAR", "source_row": "INTEGER", "account_no": "VARCHAR", "customer_id": "VARCHAR",
                  "match_status": "VARCHAR", "match_method": "VARCHAR", "business_line": "VARCHAR",
                  "bill_month": "DATE", "bill_date": "DATE", "line_rental": "DECIMAL(12,2)",
                  "call_charges": "DECIMAL(12,2)", "total": "DECIMAL(12,2)", "revenue_treatment": "VARCHAR",
                  "is_credit_note": "BOOLEAN", "bill_month_flag": "VARCHAR", "bill_date_flag": "VARCHAR",
                  "line_rental_flag": "VARCHAR", "call_charges_flag": "VARCHAR", "total_flag": "VARCHAR"},
    "fact_contract_line": {"line_id": "VARCHAR", "source_row": "INTEGER", "crm_account_id": "VARCHAR",
                           "customer_id": "VARCHAR", "match_status": "VARCHAR", "match_method": "VARCHAR",
                           "business_line": "VARCHAR", "annual_value": "DECIMAL(12,2)", "contract_start": "DATE",
                           "term_years": "INTEGER", "renewal_date": "DATE", "end_date": "DATE",
                           "annual_value_flag": "VARCHAR", "contract_start_flag": "VARCHAR",
                           "renewal_date_flag": "VARCHAR", "end_date_flag": "VARCHAR"},
    "dim_kb_article": {"kb_id": "VARCHAR", "title": "VARCHAR", "doc_type": "VARCHAR", "status": "VARCHAR",
                       "superseded_by": "VARCHAR", "superseded_on": "DATE", "created": "DATE", "updated": "DATE"},
    "fact_telecoms_contract": {"contract_ref": "VARCHAR", "source_row": "INTEGER", "account_no": "VARCHAR",
                               "customer_id": "VARCHAR", "match_status": "VARCHAR", "match_method": "VARCHAR",
                               "business_line": "VARCHAR", "service_type": "VARCHAR", "start_date": "DATE",
                               "end_date": "DATE", "start_date_flag": "VARCHAR", "end_date_flag": "VARCHAR"},
    "dq_finding": {"rule_id": "VARCHAR", "table_name": "VARCHAR", "record_key": "VARCHAR", "source_row": "VARCHAR",
                   "field": "VARCHAR", "action": "VARCHAR", "original": "VARCHAR", "detail": "VARCHAR"},
    "dq_rejected_row": {"source_system": "VARCHAR", "source_file": "VARCHAR", "source_row": "VARCHAR", "reason": "VARCHAR"},
    "dq_review_record": {"source_system": "VARCHAR", "source_table": "VARCHAR", "source_key": "VARCHAR",
                         "raw_name": "VARCHAR", "review_status": "VARCHAR", "decided_account_id": "VARCHAR",
                         "decision_level": "VARCHAR"},
    "dq_source_count": {"source_table": "VARCHAR", "source_system": "VARCHAR", "validated_rows": "INTEGER",
                        "duplicates_marked": "INTEGER", "warehouse_table": "VARCHAR", "warehouse_rows": "INTEGER",
                        "merged_into_other_rows": "INTEGER"},
}
DATE_COLUMNS = {t: [c for c, ty in s.items() if ty == "DATE"] for t, s in SCHEMAS.items() if t != "dim_date"}


def _write(con, name: str, rows: list[dict], order: list[str]) -> None:
    schema = SCHEMAS[name]
    df = pd.DataFrame(rows, columns=list(schema)).astype(object).where(lambda d: d.notna(), None)
    con.register("_src", df)
    cols = ", ".join(f'CAST("{c}" AS {t}) AS "{c}"' for c, t in schema.items())
    con.execute(f"CREATE TABLE {name} AS SELECT {cols} FROM _src ORDER BY {', '.join(order)}")
    con.unregister("_src")


def build(settings: dict, cfg: dict, validated: Path | None = None, resolved: Path | None = None,
          warehouse: Path | None = None, decisions_path: Path | None = None, staging: Path | None = None) -> dict:
    vd = validated or settings["validated_dir"]
    sd = staging or settings["staging_dir"]
    rd = resolved or settings["resolved_dir"]
    wd = warehouse or settings["warehouse_dir"]
    t = {n: _read(vd / f"{n}.csv") for n in ("crm_accounts", "crm_contract_lines", "helpdesk_tickets",
                                              "shop_orders", "shop_order_lines", "telecoms_bills",
                                              "telecoms_contracts", "kb_documents")}
    finding_rows = _read(vd / "validation_findings.csv")
    findings = Findings(finding_rows)
    crosswalk = _read(rd / "crosswalk.csv")
    dups = _read(wd / "duplicates.csv")
    dup_orders = {d["record_key"] for d in dups if d["table"] == "shop_orders"}
    dup_bill_rows = {d["source_row"] for d in dups if d["table"] == "telecoms_bills"}
    unknown = cfg["unknown_customer"]["id"]

    # dim_customer: CRM main records (each maps to itself in the crosswalk)
    crm_map = {r["source_key"]: r["resolved_account_id"] for r in crosswalk if r["source_table"] == "crm_accounts"}
    merged = defaultdict(list)
    for raw, main in crm_map.items():
        if raw != main:
            merged[main].append(raw)
    customers = [{"customer_id": a["Account ID"], "company_name": a["Company Name"], "sector": _none(a["Sector"]),
                  "account_manager": _none(a["Account Manager"]),
                  "customer_since": findings.value("crm_accounts", a["_source_row"], "Customer Since", a["Customer Since__value"]),
                  "status": a["Status"],
                  "closed_date": findings.value("crm_accounts", a["_source_row"], "Closed Date", a["Closed Date__value"]),
                  "merged_crm_records": ", ".join(sorted(merged[a["Account ID"]])) or None, "is_unknown": False}
                 for a in t["crm_accounts"] if crm_map[a["Account ID"]] == a["Account ID"]]
    customers.append({"customer_id": unknown, "company_name": cfg["unknown_customer"]["name"], "is_unknown": False})
    customers[-1]["is_unknown"] = True
    canonical = {c["customer_id"] for c in customers if not c["is_unknown"]}

    # queue units, so a decision can name a whole spelling, shop account or telecoms account
    shop_cust = {o["order_id"]: o["customer_id"] for o in t["shop_orders"]}
    bill_acct = {b["_source_row"]: b["account_no"] for b in t["telecoms_bills"]}
    contract_acct = {c["_source_row"]: c["account_no"] for c in t["telecoms_contracts"]}
    units = {}
    for r in crosswalk:
        k = (r["source_table"], r["source_row"])
        if r["source_table"] == "helpdesk_tickets":
            units[k] = r["raw_name"]
        elif r["source_table"] == "shop_orders":
            units[k] = f"shop customer {shop_cust[r['source_key']]}"
        elif r["source_table"] == "telecoms_bills":
            units[k] = bill_acct[r["source_row"]]
        elif r["source_table"] == "telecoms_contracts":
            units[k] = contract_acct[r["source_row"]]
    decisions = read_decisions(decisions_path or ROOT / cfg["review_decisions"])
    links = CustomerLinks(crosswalk, decisions, canonical, unknown, units)

    # fact_ticket and ticket_text
    tickets, texts = [], []
    for r in t["helpdesk_tickets"]:
        row, f = r["_source_row"], lambda field: findings.flag("helpdesk_tickets", r["_source_row"], field)
        v = lambda field: findings.value("helpdesk_tickets", row, field, r[f"{field}__value"])
        cust, status, method = links.link("helpdesk_tickets", row)
        opened, closed = v("opened_at"), v("closed_at")
        if r["response_sla_measurable"] == "true":
            sla_flag = None
        elif not r["closed_at"].strip():
            sla_flag = "ticket open at the as-of date: response SLA not measured"
        elif not r["first_responded_at"].strip():
            sla_flag = "closed with no first response (MISSING-001)"
        else:
            sla_flag = "a timestamp is not a valid date"
        tickets.append({"ticket_id": r["ticket_id"], "source_row": int(row), "customer_id": cust,
                        "match_status": status, "match_method": method, "business_line": "BL1",
                        "opened_at": opened, "opened_date": opened[:10] if opened else None,
                        "first_responded_at": v("first_responded_at"), "closed_at": closed,
                        "closed_date": closed[:10] if closed else None,
                        "priority": v("priority"), "category": v("category"), "agent": _none(r["agent"]),
                        "kb_article_id": _none(r["kb_article_id"]),
                        "response_sla_measurable": r["response_sla_measurable"] == "true",
                        "opened_at_flag": f("opened_at"), "first_responded_at_flag": f("first_responded_at"),
                        "closed_at_flag": f("closed_at"), "priority_flag": f("priority"),
                        "category_flag": f("category"), "response_sla_flag": sla_flag})
        texts.append({"ticket_id": r["ticket_id"], "description": _none(r["description"]),
                      "resolution_notes": _none(r["resolution_notes"])})

    # fact_order and fact_order_line (duplicate orders left out)
    orders, order_info = [], {}
    for o in t["shop_orders"]:
        if o["order_id"] in dup_orders:
            continue
        row = o["_source_row"]
        f = lambda field: findings.flag("shop_orders", row, field)
        v = lambda field: findings.value("shop_orders", row, field, o[f"{field}__value"])
        cust, status, method = links.link("shop_orders", row)
        at = v("order_date")
        order_info[o["order_id"]] = (cust, status, at[:10] if at else None, o["revenue_treatment"])
        orders.append({"order_id": o["order_id"], "shop_customer_id": o["customer_id"], "customer_id": cust,
                       "match_status": status, "match_method": method, "business_line": "BL5",
                       "ordered_at": at, "order_date": at[:10] if at else None, "status": o["status"],
                       "promised_date": v("promised_date"), "delivered_date": v("delivered_date"),
                       "order_total": v("order_total"), "line_count": _none(o["line_count"]),
                       "revenue_treatment": o["revenue_treatment"], "order_date_flag": f("order_date"),
                       "promised_date_flag": f("promised_date"), "delivered_date_flag": f("delivered_date"),
                       "order_total_flag": f("order_total")})
    lines = []
    for ln in t["shop_order_lines"]:
        if ln["order_id"] in dup_orders:
            continue
        key = f"{ln['order_id']}#{ln['line_no']}"
        f = lambda field: findings.flag("shop_order_lines", key, field)
        v = lambda field: findings.value("shop_order_lines", key, field, ln[f"{field}__value"])
        cust, status, day, treatment = order_info[ln["order_id"]]
        q = v("quantity")
        lines.append({"order_id": ln["order_id"], "line_no": int(ln["line_no"]), "customer_id": cust,
                      "match_status": status, "business_line": "BL5", "order_date": day,
                      "revenue_treatment": treatment, "sku": ln["sku"], "product": ln["product"],
                      "quantity": int(float(q)) if q is not None else None, "unit_price": v("unit_price"),
                      "line_total": v("line_total"), "quantity_flag": f("quantity"),
                      "unit_price_flag": f("unit_price"), "line_total_flag": f("line_total")})

    # fact_bill (duplicate bill rows left out)
    bills = []
    for b in t["telecoms_bills"]:
        row = b["_source_row"]
        if row in dup_bill_rows:
            continue
        f = lambda field: findings.flag("telecoms_bills", row, field)
        v = lambda field: findings.value("telecoms_bills", row, field, b[f"{field}__value"])
        cust, status, method = links.link("telecoms_bills", row)
        bills.append({"bill_id": b["bill_id"], "source_row": int(row), "account_no": b["account_no"],
                      "customer_id": cust, "match_status": status, "match_method": method, "business_line": "BL2",
                      "bill_month": v("bill_month"), "bill_date": v("bill_date"), "line_rental": v("line_rental"),
                      "call_charges": v("call_charges"), "total": v("total"),
                      "revenue_treatment": b["revenue_treatment"], "is_credit_note": b["revenue_treatment"] == "negative",
                      "bill_month_flag": f("bill_month"), "bill_date_flag": f("bill_date"),
                      "line_rental_flag": f("line_rental"), "call_charges_flag": f("call_charges"),
                      "total_flag": f("total")})

    # fact_contract_line (CRM records always resolve; duplicates go to their main record)
    contract_lines = []
    for c in t["crm_contract_lines"]:
        row = c["_source_row"]
        f = lambda field: findings.flag("crm_contract_lines", row, field)
        v = lambda field: findings.value("crm_contract_lines", row, field, c[f"{field}__value"])
        main = crm_map[c["Account ID"]]
        term = c["Term (Years)"].strip()
        contract_lines.append({"line_id": c["Line ID"], "source_row": int(row), "crm_account_id": c["Account ID"],
                               "customer_id": main, "match_status": "auto",
                               "match_method": "exact" if main == c["Account ID"] else "crm_duplicate",
                               "business_line": c["Business Line"], "annual_value": v("Annual Value (GBP)"),
                               "contract_start": v("Contract Start"), "term_years": int(term) if term.isdigit() else None,
                               "renewal_date": v("Renewal Date"), "end_date": v("End Date"),
                               "annual_value_flag": f("Annual Value (GBP)"), "contract_start_flag": f("Contract Start"),
                               "renewal_date_flag": f("Renewal Date"), "end_date_flag": f("End Date")})

    # dim_kb_article: document status only (the body stays out of the warehouse until Phase 5)
    articles = [{"kb_id": k["id"], "title": k["title"], "doc_type": k["type"], "status": k["status"],
                 "superseded_by": _none(k["superseded_by"]), "superseded_on": _none(k["superseded_on"]),
                 "created": _none(k["created"]), "updated": _none(k["updated"])} for k in t["kb_documents"]]

    # fact_telecoms_contract (BQ-13): one row per telecoms contract, linked through the crosswalk
    tel_contracts = []
    for c in t["telecoms_contracts"]:
        row = c["_source_row"]
        f = lambda field: findings.flag("telecoms_contracts", row, field)
        v = lambda field: findings.value("telecoms_contracts", row, field, c[f"{field}__value"])
        cust, status, method = links.link("telecoms_contracts", row)
        tel_contracts.append({"contract_ref": c["contract_ref"], "source_row": int(row), "account_no": c["account_no"],
                              "customer_id": cust, "match_status": status, "match_method": method,
                              "business_line": "BL2", "service_type": _none(c["service_type"]),
                              "start_date": v("start_date"), "end_date": v("end_date"),
                              "start_date_flag": f("start_date"), "end_date_flag": f("end_date")})

    # data quality (BQ-14): validation findings, rejected rows (reason only, never their content)
    dq_findings = [{"rule_id": x["rule_id"], "table_name": x["table"], "record_key": x["record_key"],
                    "source_row": x["source_row"], "field": x["field"], "action": x["action"],
                    "original": _none(x["original"]), "detail": x["detail"]} for x in finding_rows]
    rejected = [{"source_system": x["source_system"], "source_file": x["source_file"], "source_row": x["source_row"],
                 "reason": x["reason"]} for x in _read(sd / "rejected_rows.csv")]
    # review queue records (marked duplicates left out, as in the facts) and whether a person decided them
    review_records = []
    for r in crosswalk:
        k = (r["source_table"], r["source_row"])
        if r["status"] != "review" or (r["source_table"] == "shop_orders" and r["source_key"] in dup_orders) \
                or (r["source_table"] == "telecoms_bills" and r["source_row"] in dup_bill_rows):
            continue
        d = links.decided.get(k)
        review_records.append({"source_system": r["source_system"], "source_table": r["source_table"],
                               "source_key": r["source_key"], "raw_name": r["raw_name"],
                               "review_status": "decided" if d else "waiting",
                               "decided_account_id": d["decided_account_id"] if d else None,
                               "decision_level": links.decision_level[k] if d else None})
    merged_count = sum(1 for raw, main in crm_map.items() if raw != main)
    source_counts = [
        {"source_table": n, "source_system": sysname, "validated_rows": len(t[n]),
         "duplicates_marked": dupn, "warehouse_table": wt, "warehouse_rows": wn, "merged_into_other_rows": mg}
        for n, sysname, dupn, wt, wn, mg in (
            ("crm_accounts", "crm", 0, "dim_customer", len(customers) - 1, merged_count),
            ("crm_contract_lines", "crm", 0, "fact_contract_line", len(contract_lines), 0),
            ("helpdesk_tickets", "helpdesk", 0, "fact_ticket", len(tickets), 0),
            ("shop_orders", "shop", len(dup_orders), "fact_order", len(orders), 0),
            ("shop_order_lines", "shop", sum(1 for ln in t["shop_order_lines"] if ln["order_id"] in dup_orders),
             "fact_order_line", len(lines), 0),
            ("telecoms_contracts", "telecoms", 0, "fact_telecoms_contract", len(tel_contracts), 0),
            ("telecoms_bills", "telecoms", len(dup_bill_rows), "fact_bill", len(bills), 0),
            ("kb_documents", "knowledge_base", 0, "dim_kb_article", len(articles), 0))]

    # dim_date: every date that appears anywhere, start to end
    found = set()
    for name, rows in (("dim_customer", customers), ("fact_ticket", tickets), ("fact_order", orders),
                       ("fact_order_line", lines), ("fact_bill", bills), ("fact_contract_line", contract_lines),
                       ("dim_kb_article", articles), ("fact_telecoms_contract", tel_contracts)):
        for r in rows:
            found.update(r[c] for c in DATE_COLUMNS[name] if r.get(c))
    win = cfg["data_window"]
    as_of = dt.date.fromisoformat(settings["as_of_moment"][:10])
    days, d, last = [], dt.date.fromisoformat(min(found)), dt.date.fromisoformat(max(found))
    while d <= last:
        days.append({"date": d.isoformat(), "year": d.year, "quarter": (d.month - 1) // 3 + 1, "month": d.month,
                     "month_name": d.strftime("%B"), "year_month": d.strftime("%Y-%m"), "day_of_week": d.isoweekday(),
                     "day_name": d.strftime("%A"), "is_weekend": d.isoweekday() >= 6,
                     "in_data_window": win["start"] <= d.isoformat() <= win["end"], "after_as_of": d >= as_of})
        d += dt.timedelta(days=1)

    # write a fresh database, then replace the old file
    wd.mkdir(parents=True, exist_ok=True)
    final = wd / cfg["database"]
    tmp = final.with_suffix(".building.duckdb")
    tmp.unlink(missing_ok=True)
    con = duckdb.connect(str(tmp))
    try:
        _write(con, "dim_customer", customers, ["customer_id"])
        _write(con, "dim_business_line", [{k: b[k] if k != "business_line" else b["id"] for k in
                                            ("business_line", "name", "revenue_source", "modelled")}
                                           for b in cfg["business_lines"]], ["business_line"])
        _write(con, "dim_date", days, ["date"])
        _write(con, "fact_ticket", tickets, ["ticket_id"])
        _write(con, "ticket_text", texts, ["ticket_id"])
        _write(con, "fact_order", orders, ["order_id"])
        _write(con, "fact_order_line", lines, ["order_id", "line_no"])
        _write(con, "fact_bill", bills, ["source_row"])
        _write(con, "fact_contract_line", contract_lines, ["line_id"])
        _write(con, "dim_kb_article", articles, ["kb_id"])
        _write(con, "fact_telecoms_contract", tel_contracts, ["contract_ref"])
        _write(con, "dq_finding", dq_findings, ["table_name", "record_key", "field", "rule_id", "source_row"])
        _write(con, "dq_rejected_row", rejected, ["source_system", "source_file", "source_row"])
        _write(con, "dq_source_count", source_counts, ["source_table"])
        _write(con, "dq_review_record", review_records, ["source_table", "source_key"])
        create_views(con, cfg)
        create_at_risk_view(con, load_at_risk(), settings["as_of_moment"][:10])
        create_kpi_views(con, load_thresholds(), settings["as_of_moment"][:10])
        create_dashboard_views(con, settings["as_of_moment"][:10])
        summary = unknown_summary(con, unknown)
    finally:
        con.close()
    os.replace(tmp, final)
    return {"database": str(final), "rows": {n: len(r) for n, r in (
        ("dim_customer", customers), ("dim_date", days), ("fact_ticket", tickets), ("ticket_text", texts),
        ("fact_order", orders), ("fact_order_line", lines), ("fact_bill", bills),
        ("fact_contract_line", contract_lines), ("dim_kb_article", articles),
        ("fact_telecoms_contract", tel_contracts), ("dq_finding", dq_findings))}, "decisions_applied": len(links.decided), "unknown": summary}


def unknown_summary(con, unknown: str) -> list[tuple]:
    """Rows and revenue on the Unknown customer, by source, business line and match status.
    Revenue follows semantic/metrics.yaml: shop orders with revenue_treatment 'include';
    bills 'include' plus 'negative' (credit notes). Tickets carry no revenue."""
    return con.execute("""
        SELECT 'helpdesk' AS source, business_line, match_status, COUNT(*) AS rows, 0::DECIMAL(12,2) AS revenue
          FROM fact_ticket WHERE customer_id = $u GROUP BY ALL
        UNION ALL
        SELECT 'shop', business_line, match_status, COUNT(*),
               COALESCE(SUM(CASE WHEN revenue_treatment = 'include' THEN order_total END), 0)
          FROM fact_order WHERE customer_id = $u GROUP BY ALL
        UNION ALL
        SELECT 'telecoms', business_line, match_status, COUNT(*),
               COALESCE(SUM(CASE WHEN revenue_treatment IN ('include', 'negative') THEN total END), 0)
          FROM fact_bill WHERE customer_id = $u GROUP BY ALL
        ORDER BY 1, 2, 3""", {"u": unknown}).fetchall()


def main() -> None:
    out = build(load_settings(), load_config())
    print(f"Built {out['database']}")
    for n, c in out["rows"].items():
        print(f"  {n}: {c}")
    print(f"Review decisions applied to {out['decisions_applied']} records")
    print("Unknown customer (source, line, status, rows, revenue £):")
    for r in out["unknown"]:
        print(f"  {r[0]:9s} {r[1]} {r[2]:10s} {r[3]:>5} {float(r[4]):>12,.2f}")


if __name__ == "__main__":
    main()
