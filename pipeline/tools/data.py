"""Read-only data tools for the assistant (step 5.3, D-036).

A fixed list of named tools, each reading one or a few KPI views (or semantic/metrics.yaml) through a
read-only DuckDB connection. There is no free-form SQL: every query is a constant in this file, and the
only values that reach SQL are bound parameters that have already passed the tool's schema (enums,
YYYY-MM months inside the data window).

    TOOLS                      tool definitions (name, description, input_schema) in the form the Claude
                               API takes for tool use, so step 5.4 passes them as they are
    call(name, args)           run a tool; always returns a dict, never raises for bad input

Every result: {"tool", "sources" (view names to cite), "rows" (at most ROW_CAP), "row_count" (before the
cap), "truncated", "totals" (worked out here, so the model never adds up rows), "note" (optional)}.
A bad call returns {"error": {"code", "message", "parameter"?, "allowed"?}}.

Results carry customer IDs only, never business names, staff names or ticket text; names are added back
locally after the model has answered (pipeline.tools.names).
"""
from __future__ import annotations

import datetime as dt
import decimal
import re
from pathlib import Path

import duckdb
import yaml

from pipeline.settings import ROOT, load_settings
from pipeline.warehouse.load import load_config as warehouse_config

ROW_CAP = 25
METRICS_FILE = ROOT / "semantic" / "metrics.yaml"
THRESHOLDS_FILE = ROOT / "config" / "kpi_thresholds.yaml"
MONTH = {"type": "string", "pattern": r"^\d{4}-(0[1-9]|1[0-2])$",
         "description": "Calendar month as YYYY-MM, inside the data window (see as_of)."}
LIMIT = {"type": "integer", "minimum": 1, "maximum": ROW_CAP,
         "description": f"Most rows to return (default and maximum {ROW_CAP})."}
LINES = ["BL1", "BL2", "BL3", "BL4", "BL5"]
PRIORITIES = ["P1", "P2", "P3", "P4"]
ALERT_RULES = ["ticket_about_to_breach", "p1_open_2h", "customer_at_risk", "unusual_ticket_volume",
               "crm_billing_mismatch"]


def _metric_names() -> list[str]:
    with open(METRICS_FILE, encoding="utf-8") as fh:
        return sorted(yaml.safe_load(fh)["metrics"])


def _thresholds() -> dict:
    with open(THRESHOLDS_FILE, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _schema(props: dict | None = None, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props or {}, "required": required or [], "additionalProperties": False}


TOOLS: list[dict] = [
    {"name": "as_of",
     "description": "The as-of date of the data and the first and last day of the data window.",
     "input_schema": _schema()},
    {"name": "open_ticket_alerts",
     "description": "Open tickets past (breached) or close to (about to breach) their resolution target, most urgent first.",
     "input_schema": _schema({
         "alert": {"type": "string", "enum": ["breached", "about to breach", "all"], "description": "Which open tickets (default all)."},
         "priority": {"type": "string", "enum": PRIORITIES + ["all"], "description": "Ticket priority (default all)."},
         "limit": LIMIT})},
    {"name": "sla_compliance",
     "description": "Response and resolution SLA compliance of closed tickets, by priority and month closed, with totals.",
     "input_schema": _schema({
         "priority": {"type": "string", "enum": PRIORITIES + ["all"], "description": "Ticket priority (default all)."},
         "year_month": dict(MONTH, description="Only this closing month (default: every month in the window)."),
         "limit": LIMIT})},
    {"name": "at_risk_customers",
     "description": "Customers scored on the three at-risk signals (tickets rising, BL2 spend falling, shop spend falling), by retention priority.",
     "input_schema": _schema({
         "only_at_risk": {"type": "boolean", "description": "Only customers flagged at risk (default true)."},
         "limit": LIMIT})},
    {"name": "unusual_ticket_volume",
     "description": "Customers with an unusual number of tickets in a month (threshold in config/kpi_thresholds.yaml).",
     "input_schema": _schema({
         "year_month": dict(MONTH, description="Month to check (default: the last month of the window)."),
         "limit": LIMIT})},
    {"name": "revenue_by_line",
     "description": "Revenue for the whole window by business line, with the total, the modelled/measured split and revenue on the Unknown customer.",
     "input_schema": _schema()},
    {"name": "revenue_monthly",
     "description": "Revenue by business line and month, with totals for the rows asked for.",
     "input_schema": _schema({
         "business_line": {"type": "string", "enum": LINES + ["all"], "description": "Business line (default all)."},
         "from_month": dict(MONTH, description="First month (default: start of the window)."),
         "to_month": dict(MONTH, description="Last month (default: end of the window)."),
         "limit": LIMIT})},
    {"name": "renewals",
     "description": "Contract lines renewing in the next 90 days after the as-of date, and active lines with no renewal date.",
     "input_schema": _schema({
         "status": {"type": "string", "enum": ["renews in next 90 days", "renewal date missing", "all"], "description": "Which lines (default all)."},
         "business_line": {"type": "string", "enum": LINES + ["all"], "description": "Business line (default all)."},
         "limit": LIMIT})},
    {"name": "onboarding",
     "description": "New customers' first 30 days: tickets and P1/P2 tickets, and whether onboarding is off track.",
     "input_schema": _schema({
         "only_off_track": {"type": "boolean", "description": "Only customers off track (default false)."},
         "limit": LIMIT})},
    {"name": "crm_billing_mismatches",
     "description": "BQ-13 check of CRM BL2 contract lines against telecoms billing accounts.",
     "input_schema": _schema({
         "check_result": {"type": "string",
                          "enum": ["mismatches", "dates differ by more than 30 days", "BL2 line in CRM, no telecoms account",
                                   "telecoms account, no BL2 line in CRM", "matched, dates agree", "all"],
                          "description": "Which results (default mismatches: everything except matched, dates agree)."},
         "limit": LIMIT})},
    {"name": "active_alerts",
     "description": "Alerts active after the last alert run, most severe first.",
     "input_schema": _schema({
         "rule_id": {"type": "string", "enum": ALERT_RULES + ["all"], "description": "Alert rule (default all)."},
         "severity": {"type": "string", "enum": ["high", "medium", "low", "all"], "description": "Severity (default all)."},
         "limit": LIMIT})},
    {"name": "data_quality_summary",
     "description": "Data quality: overview (review queue and revenue on Unknown), validation findings, customer matching, or source row counts.",
     "input_schema": _schema({
         "section": {"type": "string", "enum": ["overview", "findings", "matching", "sources", "review_queue"],
                     "description": "Which part (default overview)."},
         "limit": LIMIT})},
    {"name": "metric_definition",
     "description": "The agreed plain-English definition of a metric, the numbers it uses and where they come from.",
     "input_schema": _schema({"name": {"type": "string", "enum": _metric_names(), "description": "Metric name."}},
                             required=["name"])},
]
_BY_NAME = {t["name"]: t for t in TOOLS}


# ---- helpers -------------------------------------------------------------------------------------

class _BadCall(Exception):
    def __init__(self, code: str, message: str, **extra):
        super().__init__(message)
        self.payload = {"code": code, "message": message, **extra}


def _check(schema: dict, args: dict) -> dict:
    """Check args against one of the schemas above (the small subset of JSON Schema they use)."""
    if not isinstance(args, dict):
        raise _BadCall("bad_parameter", "Arguments must be an object.")
    props = schema["properties"]
    for key in args:
        if key not in props:
            raise _BadCall("bad_parameter", f"Unknown parameter '{key}'.", parameter=key, allowed=sorted(props))
    for key in schema["required"]:
        if key not in args:
            raise _BadCall("bad_parameter", f"Missing required parameter '{key}'.", parameter=key)
    for key, value in args.items():
        p = props[key]
        ok = {"string": isinstance(value, str), "boolean": isinstance(value, bool),
              "integer": isinstance(value, int) and not isinstance(value, bool)}[p["type"]]
        if not ok:
            raise _BadCall("bad_parameter", f"'{key}' must be a {p['type']}.", parameter=key)
        if "enum" in p and value not in p["enum"]:
            raise _BadCall("bad_parameter", f"'{value}' is not an allowed value for '{key}'.", parameter=key, allowed=p["enum"])
        if "pattern" in p and not re.fullmatch(p["pattern"], value):
            raise _BadCall("bad_parameter", f"'{key}' must be a month written YYYY-MM.", parameter=key)
        if "minimum" in p and not p["minimum"] <= value <= p["maximum"]:
            raise _BadCall("bad_parameter", f"'{key}' must be from {p['minimum']} to {p['maximum']}.", parameter=key)
    return args


def _plain(v):
    if isinstance(v, decimal.Decimal):
        return round(float(v), 2)
    if isinstance(v, float):
        return round(v, 4)
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    return v


class _Db:
    def __init__(self, path: Path):
        if not Path(path).exists():
            raise _BadCall("data_unavailable", f"{Path(path).name} has not been built; run python -m pipeline.run.")
        self.con = duckdb.connect(str(path), read_only=True)

    def rows(self, sql: str, params=()) -> list[dict]:
        cur = self.con.execute(sql, list(params))
        cols = [d[0] for d in cur.description]
        return [{c: _plain(v) for c, v in zip(cols, r)} for r in cur.fetchall()]

    def one(self, sql: str, params=()) -> dict:
        return self.rows(sql, params)[0]


def _result(tool: str, sources: list[str], rows: list[dict], totals: dict | None = None, limit: int = ROW_CAP,
            note: str | None = None) -> dict:
    out = {"tool": tool, "sources": sources, "row_count": len(rows), "truncated": len(rows) > limit,
           "rows": rows[:limit], "totals": totals or {}}
    if out["truncated"]:
        out["note"] = f"Showing the first {limit} of {len(rows)} rows; the totals cover all {len(rows)}."
    if note:
        out["note"] = f"{out.get('note', '')} {note}".strip()
    return out


def _window(w: _Db) -> tuple[str, str]:
    m = w.one("SELECT strftime(window_start, '%Y-%m') AS s, strftime(window_end, '%Y-%m') AS e FROM v_meta")
    return m["s"], m["e"]


def _month_in_window(w: _Db, key: str, value: str | None) -> None:
    if value is None:
        return
    start, end = _window(w)
    if not start <= value <= end:
        raise _BadCall("bad_parameter", f"'{key}' {value} is outside the data window {start} to {end}.", parameter=key)


def _all(value, default="all"):
    """Map 'all' (or nothing) to NULL so one constant query serves every choice: (? IS NULL OR col = ?)."""
    value = default if value is None else value
    return None if value == "all" else value


# ---- tools ---------------------------------------------------------------------------------------

def _as_of(w, a, al):
    m = w.one("SELECT as_of_date, window_start, window_end, synthetic_notice FROM v_meta")
    return _result("as_of", ["v_meta"], [], totals=m)


def _open_ticket_alerts(w, a, al):
    alert, prio = _all(a.get("alert")), _all(a.get("priority"))
    rows = w.rows("""SELECT ticket_id, customer_id, priority, category, opened_at, round(age_hours, 1) AS age_hours,
                            resolution_target_hours, round(share_of_target_used, 2) AS share_of_target_used, alert,
                            p1_management_escalation
                     FROM v_kpi_bq02_open_tickets
                     WHERE (? IS NULL OR alert = ?) AND (? IS NULL OR priority = ?)
                     ORDER BY share_of_target_used DESC, ticket_id""", [alert, alert, prio, prio])
    totals = {"matching_tickets": len(rows), "breached": sum(r["alert"] == "breached" for r in rows),
              "about_to_breach": sum(r["alert"] == "about to breach" for r in rows),
              "all_open_tickets": w.one("SELECT * FROM v_kpi_bq02_summary")}
    return _result("open_ticket_alerts", ["v_kpi_bq02_open_tickets", "v_kpi_bq02_summary"], rows, totals,
                   a.get("limit", ROW_CAP))


def _sla_compliance(w, a, al):
    prio, month = _all(a.get("priority")), a.get("year_month")
    _month_in_window(w, "year_month", month)
    where = "WHERE priority IN ('P1','P2','P3','P4') AND (? IS NULL OR priority = ?) AND (? IS NULL OR year_month = ?)"
    params = [prio, prio, month, month]
    rows = w.rows(f"""SELECT year_month, priority, closed_tickets, response_measured, response_breached,
                             round(response_compliance, 4) AS response_compliance, resolution_measured,
                             resolution_breached, round(resolution_compliance, 4) AS resolution_compliance
                      FROM v_kpi_bq01_sla_monthly {where} ORDER BY year_month DESC, priority""", params)
    t = w.one(f"""SELECT SUM(closed_tickets) AS closed_tickets,
                         SUM(response_measured) AS response_measured, SUM(response_breached) AS response_breached,
                         SUM(response_excluded) AS response_excluded,
                         SUM(resolution_measured) AS resolution_measured, SUM(resolution_breached) AS resolution_breached,
                         SUM(resolution_excluded) AS resolution_excluded,
                         round(SUM(response_met) / NULLIF(SUM(response_measured), 0), 4) AS response_compliance,
                         round(SUM(resolution_met) / NULLIF(SUM(resolution_measured), 0), 4) AS resolution_compliance
                  FROM v_kpi_bq01_sla_monthly {where}""", params)
    return _result("sla_compliance", ["v_kpi_bq01_sla_monthly"], rows, t, a.get("limit", ROW_CAP),
                   "Compliance is a share from 0 to 1 of measured tickets; excluded tickets are counted, not scored.")


def _at_risk_customers(w, a, al):
    only = a.get("only_at_risk", True)
    rows = w.rows("""SELECT customer_id, retention_rank, at_risk_score, signals_fired, at_risk,
                            tickets_prev, tickets_last, round(ticket_ratio, 2) AS ticket_ratio,
                            bl2_prev, bl2_last, round(bl2_ratio, 2) AS bl2_ratio,
                            shop_prev, shop_last, round(shop_ratio, 2) AS shop_ratio, annual_contract_value
                     FROM v_at_risk WHERE status = 'active' AND (NOT ? OR at_risk)
                     ORDER BY retention_rank NULLS LAST, at_risk_score DESC, customer_id""", [only])
    totals = w.one("SELECT customers_at_risk, active_customers FROM v_kpi_bq06_summary")
    return _result("at_risk_customers", ["v_at_risk", "v_kpi_bq06_summary"], rows, totals, a.get("limit", ROW_CAP),
                   "'prev' is April to June 2026 and 'last' is July to September 2026 (config/at_risk.yaml).")


def _unusual_ticket_volume(w, a, al):
    month = a.get("year_month") or _window(w)[1]
    _month_in_window(w, "year_month", month)
    rows = w.rows("""SELECT customer_id, year_month, tickets FROM v_kpi_bq04_ticket_volume
                     WHERE unusual_volume AND year_month = ? ORDER BY tickets DESC, customer_id""", [month])
    totals = {"year_month": month, "customers_flagged": len(rows), "tickets_from_flagged": sum(r["tickets"] for r in rows),
              "all_tickets_in_month": w.one("SELECT COALESCE(SUM(tickets), 0) AS n FROM v_kpi_bq04_ticket_volume "
                                            "WHERE year_month = ?", [month])["n"]}
    return _result("unusual_ticket_volume", ["v_kpi_bq04_ticket_volume"], rows, totals, a.get("limit", ROW_CAP),
                   _thresholds()["bq04_unusual_volume"]["definition"])


def _revenue_by_line(w, a, al):
    rows = w.rows("""SELECT business_line, line_label, revenue_basis, round(revenue, 2) AS revenue,
                            round(revenue_on_unknown, 2) AS revenue_on_unknown
                     FROM v_dash_revenue_by_line ORDER BY business_line""")
    totals = w.one("""SELECT round(revenue_total, 2) AS revenue_total, round(revenue_modelled, 2) AS revenue_modelled,
                             round(revenue_measured, 2) AS revenue_measured, round(revenue_on_unknown, 2) AS revenue_on_unknown
                      FROM v_dash_revenue_total""")
    return _result("revenue_by_line", ["v_dash_revenue_by_line", "v_dash_revenue_total"], rows, totals,
                   note="GBP excluding VAT; each figure is rounded to the penny, so lines may differ from the total by a penny. 'modelled' lines are spread from annual contract values (D-007); "
                        "'measured' lines come from bills and orders.")


def _revenue_monthly(w, a, al):
    line = _all(a.get("business_line"))
    start, end = _window(w)
    lo, hi = a.get("from_month") or start, a.get("to_month") or end
    _month_in_window(w, "from_month", lo)
    _month_in_window(w, "to_month", hi)
    if lo > hi:
        raise _BadCall("bad_parameter", f"from_month {lo} is after to_month {hi}.", parameter="from_month")
    where = "WHERE (? IS NULL OR business_line = ?) AND year_month BETWEEN ? AND ?"
    params = [line, line, lo, hi]
    rows = w.rows(f"""SELECT business_line, year_month, revenue_basis, round(revenue, 2) AS revenue,
                             round(revenue_on_unknown, 2) AS revenue_on_unknown
                      FROM v_kpi_bq07_revenue_monthly {where} ORDER BY year_month, business_line""", params)
    # exact decimal sum, so the total does not depend on row order (D-027)
    totals = w.one(f"""SELECT round(SUM(CAST(revenue AS DECIMAL(38,10))), 2) AS revenue,
                              round(SUM(CAST(revenue_on_unknown AS DECIMAL(38,10))), 2) AS revenue_on_unknown
                       FROM v_kpi_bq07_revenue_monthly {where}""", params)
    totals.update(from_month=lo, to_month=hi, business_line=a.get("business_line", "all"))
    return _result("revenue_monthly", ["v_kpi_bq07_revenue_monthly"], rows, totals, a.get("limit", ROW_CAP),
                   "GBP excluding VAT.")


def _renewals(w, a, al):
    status, line = _all(a.get("status")), _all(a.get("business_line"))
    where = "WHERE (? IS NULL OR renewal_status = ?) AND (? IS NULL OR business_line = ?)"
    params = [status, status, line, line]
    n = _thresholds()["next_90_days"]
    rows = w.rows(f"""SELECT line_id, customer_id, business_line, annual_value, renewal_date, renewal_status
                      FROM v_kpi_bq05_renewals {where} ORDER BY renewal_date NULLS LAST, line_id""", params)
    by = w.rows(f"""SELECT renewal_status, COUNT(*) AS lines, SUM(annual_value) AS annual_value
                    FROM v_kpi_bq05_renewals {where} GROUP BY renewal_status ORDER BY renewal_status""", params)
    return _result("renewals", ["v_kpi_bq05_renewals"], rows, {"lines": len(rows), "by_status": by},
                   a.get("limit", ROW_CAP), f"Next 90 days means renewal dates from {n['start']} to {n['end']} inclusive "
                                            "(config/kpi_thresholds.yaml).")


def _onboarding(w, a, al):
    only = a.get("only_off_track", False)
    rows = w.rows("""SELECT customer_id, first_contract_start, tickets_first_30_days, p1_p2_first_30_days, off_track
                     FROM v_kpi_bq16_onboarding WHERE (NOT ? OR off_track)
                     ORDER BY first_contract_start, customer_id""", [only])
    t = w.one("SELECT COUNT(*) AS new_customers, COUNT(*) FILTER (WHERE off_track) AS off_track FROM v_kpi_bq16_onboarding")
    return _result("onboarding", ["v_kpi_bq16_onboarding"], rows, t, a.get("limit", ROW_CAP),
                   " ".join(_thresholds()["bq16_onboarding"]["definition"].split()))


def _crm_billing_mismatches(w, a, al):
    choice = a.get("check_result", "mismatches")
    mism, exact = choice == "mismatches", _all(None if choice == "mismatches" else choice)
    rows = w.rows("""SELECT customer_id, line_id, contract_ref, account_no, crm_end_or_renewal_date, telecoms_end_date,
                            days_difference, check_result
                     FROM v_kpi_bq13_crm_telecoms
                     WHERE (NOT ? OR check_result <> 'matched, dates agree') AND (? IS NULL OR check_result = ?)
                     ORDER BY check_result, customer_id, line_id""", [mism, exact, exact])
    by = w.rows("SELECT check_result, COUNT(*) AS records FROM v_kpi_bq13_crm_telecoms GROUP BY 1 ORDER BY 1")
    return _result("crm_billing_mismatches", ["v_kpi_bq13_crm_telecoms"], rows,
                   {"matching_records": len(rows), "all_records_by_result": by}, a.get("limit", ROW_CAP))


def _active_alerts(w, a, al):
    if al is None:
        raise _BadCall("data_unavailable", "The alert history has not been built; run python -m pipeline.alerts.run.")
    rule, sev = _all(a.get("rule_id")), _all(a.get("severity"))
    rows = al.rows("""SELECT rule_id, bq, severity, record_key, customer_id, reason, first_raised_at, times_raised, data_as_of
                      FROM v_alerts_active WHERE (? IS NULL OR rule_id = ?) AND (? IS NULL OR severity = ?)
                      ORDER BY severity_rank, rule_id, record_key""", [rule, rule, sev, sev])
    totals = {"matching_alerts": len(rows),
              "summary": al.one("SELECT active, active_high, active_medium, active_low, last_ok_run_at, last_run_status "
                                "FROM v_alerts_summary"),
              "active_by_rule": al.rows("SELECT rule_id, active FROM v_alerts_by_rule ORDER BY rule_id")}
    return _result("active_alerts", ["v_alerts_active", "v_alerts_summary", "v_alerts_by_rule"], rows, totals,
                   a.get("limit", ROW_CAP))


def _data_quality_summary(w, a, al):
    section, limit = a.get("section", "overview"), a.get("limit", ROW_CAP)
    if section == "overview":
        t = w.one("""SELECT records_waiting, records_decided, names_waiting, records_on_unknown,
                            round(revenue_on_unknown, 2) AS revenue_on_unknown FROM v_kpi_bq14_review_summary""")
        t.update(w.one("SELECT SUM(findings) AS validation_findings, COUNT(*) AS finding_types FROM v_kpi_bq14_findings"))
        t.update(w.one("SELECT COUNT(*) FILTER (WHERE NOT reconciles) AS sources_not_reconciling, "
                       "SUM(rejected_rows_for_system) AS rejected_rows FROM v_kpi_bq14_sources"))
        return _result("data_quality_summary", ["v_kpi_bq14_review_summary", "v_kpi_bq14_findings", "v_kpi_bq14_sources"],
                       [], t)
    if section == "findings":
        rows = w.rows("""SELECT rule_id, table_name, field, action, findings FROM v_kpi_bq14_findings
                         ORDER BY findings DESC, rule_id, table_name, field""")
        return _result("data_quality_summary", ["v_kpi_bq14_findings"], rows,
                       {"findings": sum(r["findings"] for r in rows), "finding_types": len(rows)}, limit)
    if section == "matching":
        rows = w.rows("SELECT source, match_status, match_label, records FROM v_kpi_bq14_matching ORDER BY source, match_status")
        return _result("data_quality_summary", ["v_kpi_bq14_matching"], rows,
                       {"records": sum(r["records"] for r in rows)}, limit)
    if section == "sources":
        rows = w.rows("""SELECT source_table, source_system, validated_rows, duplicates_marked, warehouse_table,
                                warehouse_rows, merged_into_other_rows, reconciles, rejected_rows_for_system
                         FROM v_kpi_bq14_sources ORDER BY source_table""")
        return _result("data_quality_summary", ["v_kpi_bq14_sources"], rows,
                       {"sources": len(rows), "not_reconciling": sum(not r["reconciles"] for r in rows)}, limit)
    # review_queue: counts by source system only; the names as written are business names and stay local
    rows = w.rows("""SELECT source_system, COUNT(*) AS names, SUM(records) AS records, SUM(waiting) AS waiting,
                            SUM(decided) AS decided FROM v_kpi_bq14_review_queue GROUP BY 1 ORDER BY 1""")
    return _result("data_quality_summary", ["v_kpi_bq14_review_queue", "v_kpi_bq14_review_summary"], rows,
                   w.one("SELECT records_waiting, records_decided, names_waiting FROM v_kpi_bq14_review_summary"), limit,
                   "Counts only; the names in the queue are not shared.")


def _metric_definition(w, a, al):
    with open(METRICS_FILE, encoding="utf-8") as fh:
        m = yaml.safe_load(fh)["metrics"][a["name"]]
    keep = {k: m[k] for k in ("name", "definition", "numbers", "bqs") if k in m}
    return _result("metric_definition", ["semantic/metrics.yaml"], [], keep)


_RUN = {"as_of": _as_of, "open_ticket_alerts": _open_ticket_alerts, "sla_compliance": _sla_compliance,
        "at_risk_customers": _at_risk_customers, "unusual_ticket_volume": _unusual_ticket_volume,
        "revenue_by_line": _revenue_by_line, "revenue_monthly": _revenue_monthly, "renewals": _renewals,
        "onboarding": _onboarding, "crm_billing_mismatches": _crm_billing_mismatches, "active_alerts": _active_alerts,
        "data_quality_summary": _data_quality_summary, "metric_definition": _metric_definition}
assert set(_RUN) == set(_BY_NAME)


def default_paths() -> tuple[Path, Path]:
    s = load_settings()
    return s["warehouse_dir"] / warehouse_config()["database"], s["alerts_dir"] / "alerts.duckdb"


def call(name: str, args: dict | None = None, warehouse_db: Path | None = None, alerts_db: Path | None = None) -> dict:
    """Run one tool. Bad input comes back as {"error": {...}} for the model to read; it never raises for it."""
    if name not in _BY_NAME:
        return {"error": {"code": "unknown_tool", "message": f"There is no tool called '{name}'.", "allowed": sorted(_BY_NAME)}}
    w = al = None
    try:
        args = _check(_BY_NAME[name]["input_schema"], {} if args is None else args)
        if warehouse_db is None or alerts_db is None:
            dw, da = default_paths()
            warehouse_db, alerts_db = warehouse_db or dw, alerts_db or da
        w = _Db(warehouse_db)
        if name == "active_alerts" and Path(alerts_db).exists():
            al = _Db(alerts_db)
        return _RUN[name](w, args, al)
    except _BadCall as exc:
        return {"error": exc.payload}
    finally:
        for db in (w, al):
            if db is not None:
                db.con.close()


def main() -> None:
    import json
    import sys
    name = sys.argv[1] if len(sys.argv) > 1 else "as_of"
    args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    print(json.dumps(call(name, args), indent=2))


if __name__ == "__main__":
    main()
