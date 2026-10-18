"""Read-only data tools for the assistant (step 5.3, D-036)."""
import ast
import json
import re

import duckdb
import pytest
from fastapi.testclient import TestClient

from generator.config import load_config
from generator.messiness import build as build_raw
from pipeline.alerts.run import run as run_alerts
from pipeline.ingest.run import ingest
from pipeline.resolve.run import load_config as load_resolution, resolve
from pipeline.settings import ROOT, load_settings
from pipeline.tools import data as tools
from pipeline.tools.names import add_names, lookup
from pipeline.validate.run import load_rules, validate
from pipeline.warehouse.dedup import load_config as load_warehouse, run as find_duplicates
from pipeline.warehouse.load import build as build_warehouse
from shop_api.app import create_app


@pytest.fixture(scope="module")
def dbs(tmp_path_factory):
    m = tmp_path_factory.mktemp
    raw, truth, staging, validated, resolved, warehouse, alerts = (
        m("raw"), m("truth"), m("staging"), m("validated"), m("resolved"), m("warehouse"), m("alerts"))
    build_raw(load_config(), raw, truth)
    s = load_settings()
    s = dict(s, staging_dir=staging, shop_api=dict(s["shop_api"], base_url="http://testserver"))
    ingest(s, client=TestClient(create_app(raw / "shop_orders.json"), base_url="http://testserver"),
           raw_dir=raw, staging_dir=staging)
    validate(s, load_rules(), staging, validated)
    resolve(s, load_resolution(), validated, resolved)
    find_duplicates(s, load_warehouse(), validated, warehouse)
    build_warehouse(s, load_warehouse(), validated, resolved, warehouse)
    w, a = warehouse / "northgate.duckdb", alerts / "alerts.duckdb"
    run_alerts(s, warehouse_db=w, alerts_db=a)
    return w, a


def call(dbs, name, args=None):
    return tools.call(name, args, warehouse_db=dbs[0], alerts_db=dbs[1])


def q(path, sql):
    con = duckdb.connect(str(path), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def every_call():
    """Each tool with no arguments, and once with every enum value of each parameter."""
    for t in tools.TOOLS:
        props = t["input_schema"]["properties"]
        if not t["input_schema"]["required"]:
            yield t["name"], {}
        for key, p in props.items():
            for v in p.get("enum", [True, False] if p["type"] == "boolean" else []):
                yield t["name"], {key: v}


# ---- definitions ------------------------------------------------------------------------------------

def test_tool_definitions_are_ready_for_the_claude_api():
    names = [t["name"] for t in tools.TOOLS]
    assert len(names) == len(set(names)) == 13
    for t in tools.TOOLS:
        assert set(t) == {"name", "description", "input_schema"}
        assert re.fullmatch(r"[a-z_]{1,64}", t["name"])
        assert t["description"] and "\n" not in t["description"] and len(t["description"]) <= 160
        s = t["input_schema"]
        assert s["type"] == "object" and s["additionalProperties"] is False and set(s["required"]) <= set(s["properties"])
        for key, p in s["properties"].items():
            assert p["type"] in {"string", "integer", "boolean"} and p["description"], (t["name"], key)
            if p["type"] == "string":
                assert "enum" in p or "pattern" in p, (t["name"], key)      # no free-text parameter anywhere
    json.dumps(tools.TOOLS)


def test_every_query_is_a_constant_in_the_code():
    """No free-form SQL: each .rows()/.one() query is a string literal, or an f-string whose only
    placeholder is a constant WHERE clause defined in the same function."""
    tree = ast.parse((ROOT / "pipeline" / "tools" / "data.py").read_text())
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") in {"rows", "one"}
             and getattr(n.func.value, "id", "") != "self"]                     # _Db.one passing its own query on
    assert len(calls) > 20
    wheres = [n for n in ast.walk(tree) if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "where"]
    assert wheres and all(isinstance(n.value, ast.Constant) for n in wheres)
    for c in calls:
        sql = c.args[0]
        if isinstance(sql, ast.JoinedStr):
            assert all(isinstance(v.value, ast.Name) and v.value.id == "where"
                       for v in sql.values if isinstance(v, ast.FormattedValue)), ast.unparse(sql)
        else:
            assert isinstance(sql, ast.Constant) and isinstance(sql.value, str), ast.unparse(sql)


# ---- results ----------------------------------------------------------------------------------------

def test_every_tool_runs_for_every_allowed_value(dbs):
    for name, args in every_call():
        if name == "metric_definition":
            continue
        r = call(dbs, name, args)
        assert "error" not in r, (name, args, r)
        assert set(r) >= {"tool", "sources", "rows", "row_count", "truncated", "totals"}
        assert len(r["rows"]) <= tools.ROW_CAP and r["truncated"] == (r["row_count"] > len(r["rows"]))
        json.dumps(r)
    for metric in tools._metric_names():
        r = call(dbs, "metric_definition", {"name": metric})
        assert r["totals"]["definition"] and r["sources"] == ["semantic/metrics.yaml"]


def test_no_result_contains_a_name_or_ticket_text(dbs):
    w = dbs[0]
    names = {n for (n,) in q(w, "SELECT company_name FROM dim_customer WHERE company_name IS NOT NULL")}
    names |= {n for (n,) in q(w, "SELECT name_as_written FROM v_kpi_bq14_review_queue")}
    staff = {n for (n,) in q(w, "SELECT DISTINCT account_manager FROM dim_customer WHERE account_manager IS NOT NULL")}
    text = {t for row in q(w, "SELECT description, resolution_notes FROM ticket_text") for t in row if t and len(t) >= 25}
    assert len(names) > 100 and staff and len(text) > 100
    for name, args in every_call():
        if name == "metric_definition":
            args = {"name": "at_risk_pattern"}
        out = json.dumps(call(dbs, name, dict(args, **({} if "limit" not in
                         tools._BY_NAME[name]["input_schema"]["properties"] else {"limit": tools.ROW_CAP})))).lower()
        for n in names | staff:
            assert n.lower() not in out, (name, args)
        for t in text:
            assert t.lower() not in out, (name, args)


def test_totals_agree_with_the_views(dbs):
    w, a = dbs
    total = q(w, "SELECT revenue_total, revenue_on_unknown FROM v_dash_revenue_total")[0]
    r = call(dbs, "revenue_by_line")
    assert r["totals"]["revenue_total"] == round(total[0], 2) and r["totals"]["revenue_on_unknown"] == round(total[1], 2)
    assert call(dbs, "revenue_monthly")["totals"]["revenue"] == round(total[0], 2)
    for bl, rev in q(w, "SELECT business_line, revenue FROM v_dash_revenue_by_line"):
        assert call(dbs, "revenue_monthly", {"business_line": bl})["totals"]["revenue"] == round(rev, 2)
    sla = call(dbs, "sla_compliance")["totals"]
    closed, breached = q(w, "SELECT SUM(closed_tickets), SUM(resolution_breached) FROM v_kpi_bq01_summary "
                            "WHERE priority IN ('P1','P2','P3','P4')")[0]
    assert (sla["closed_tickets"], sla["resolution_breached"]) == (closed, breached)
    by = {x["renewal_status"]: x["lines"] for x in call(dbs, "renewals")["totals"]["by_status"]}
    assert by == dict(q(w, "SELECT renewal_status, COUNT(*) FROM v_kpi_bq05_renewals GROUP BY 1"))
    assert call(dbs, "active_alerts")["totals"]["matching_alerts"] == q(a, "SELECT active FROM v_alerts_summary")[0][0]
    assert call(dbs, "open_ticket_alerts")["totals"]["matching_tickets"] == q(w, "SELECT open_tickets FROM v_kpi_bq02_summary")[0][0]
    assert call(dbs, "at_risk_customers")["row_count"] == q(w, "SELECT customers_at_risk FROM v_kpi_bq06_summary")[0][0]
    end = q(w, "SELECT strftime(window_end, '%Y-%m') FROM v_meta")[0][0]
    assert call(dbs, "unusual_ticket_volume")["totals"]["year_month"] == end
    assert call(dbs, "as_of")["totals"]["as_of_date"] == "2026-09-30"


def test_capped_results_say_so_and_keep_full_totals(dbs):
    full, cut = call(dbs, "renewals"), call(dbs, "renewals", {"limit": 3})
    assert len(cut["rows"]) == 3 and cut["truncated"] and cut["row_count"] == full["row_count"] > 3
    assert cut["totals"] == full["totals"] and "first 3 of" in cut["note"]
    assert len(full["rows"]) == tools.ROW_CAP and full["truncated"]


@pytest.mark.parametrize("name,args,code", [
    ("no_such_tool", {}, "unknown_tool"),
    ("renewals", {"customer": "ACC-0001"}, "bad_parameter"),
    ("renewals", {"status": "x'; DROP TABLE fact_ticket; --"}, "bad_parameter"),
    ("sla_compliance", {"priority": "P5"}, "bad_parameter"),
    ("sla_compliance", {"year_month": "2026-13"}, "bad_parameter"),
    ("sla_compliance", {"year_month": "2024-01"}, "bad_parameter"),
    ("revenue_monthly", {"from_month": "2026-06", "to_month": "2026-01"}, "bad_parameter"),
    ("onboarding", {"limit": "5"}, "bad_parameter"),
    ("onboarding", {"limit": True}, "bad_parameter"),
    ("onboarding", {"limit": 26}, "bad_parameter"),
    ("onboarding", {"only_off_track": "yes"}, "bad_parameter"),
    ("metric_definition", {}, "bad_parameter"),
    ("as_of", "not an object", "bad_parameter"),
])
def test_bad_calls_return_an_error_object(dbs, name, args, code):
    r = call(dbs, name, args)
    assert set(r) == {"error"} and r["error"]["code"] == code and r["error"]["message"]
    json.dumps(r)


def test_missing_database_is_an_error_object(tmp_path):
    r = tools.call("as_of", {}, warehouse_db=tmp_path / "none.duckdb", alerts_db=tmp_path / "none2.duckdb")
    assert r["error"]["code"] == "data_unavailable"


def test_tools_do_not_change_the_databases(dbs):
    before = [p.stat().st_mtime_ns for p in dbs]
    for name, args in list(every_call())[:30]:
        call(dbs, name, args)
    assert [p.stat().st_mtime_ns for p in dbs] == before


def test_names_are_added_locally_after_the_answer(dbs):
    names = lookup(dbs[0])
    cid, name = sorted(names.items())[0]
    assert add_names(f"{cid} is at risk; UNKNOWN and ACC-9999 stay.", names) == \
        f"{name} ({cid}) is at risk; UNKNOWN and ACC-9999 stay."
