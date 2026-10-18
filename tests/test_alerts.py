"""Alert runner (step 5.1, D-034): rules read KPI views only, counts match the views, repeat runs
keep history without duplicates, cleared alerts are kept and can reopen, failures are logged."""
import datetime as dt

import duckdb
import pytest
from fastapi.testclient import TestClient

from generator.config import load_config
from generator.messiness import build as build_raw
from pipeline.alerts.run import load_rules, run
from pipeline.ingest.run import ingest
from pipeline.resolve.run import load_config as load_resolution, resolve
from pipeline.settings import load_settings
from pipeline.validate.run import load_rules as validation_rules, validate
from pipeline.warehouse.dedup import run as find_duplicates
from pipeline.warehouse.load import DECISION_COLUMNS, build, load_config as load_warehouse
from shop_api.app import create_app

T0 = dt.datetime(2026, 10, 19, 7, 0)


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    m = tmp_path_factory.mktemp
    raw, truth, staging, validated, resolved, wh = m("raw"), m("truth"), m("staging"), m("validated"), m("resolved"), m("wh")
    build_raw(load_config(), raw, truth)
    s = load_settings()
    s = dict(s, staging_dir=staging, warehouse_dir=wh, shop_api=dict(s["shop_api"], base_url="http://testserver"))
    ingest(s, client=TestClient(create_app(raw / "shop_orders.json"), base_url="http://testserver"),
           raw_dir=raw, staging_dir=staging)
    validate(s, validation_rules(), staging, validated)
    resolve(s, load_resolution(), validated, resolved)
    find_duplicates(s, load_warehouse(), validated, wh)
    empty = m("d") / "d.csv"
    empty.write_text(",".join(DECISION_COLUMNS) + "\n", encoding="utf-8")
    build(s, load_warehouse(), validated, resolved, wh, empty)
    return {"s": s, "wdb": wh / "northgate.duckdb", "m": m}


def _q(db, sql, params=None):
    con = duckdb.connect(str(db), read_only=True)
    try:
        return con.execute(sql, params or []).fetchall()
    finally:
        con.close()


def test_rules_only_read_views_that_exist(env):
    views = {r[0] for r in _q(env["wdb"], "SELECT table_name FROM information_schema.tables WHERE table_type = 'VIEW'")}
    for r in load_rules():
        assert r["view"] in views and r["view"].startswith("v_"), r["id"]
    assert {r["id"] for r in load_rules()} == {"ticket_about_to_breach", "p1_open_2h", "customer_at_risk",
                                               "unusual_ticket_volume", "crm_billing_mismatch"}


@pytest.mark.parametrize("bad", [{"view": "fact_ticket"}, {"view": "v_at_risk; DROP TABLE x"}, {"where": "1=1; DELETE"}])
def test_a_rule_that_is_not_a_view_filter_is_refused(tmp_path, bad):
    import yaml
    rule = dict(load_rules()[0], **bad)
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump({"rules": [rule]}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_rules(path)


def test_first_run_counts_match_the_views(env):
    adb = env["m"]("a1") / "alerts.duckdb"
    out = run(env["s"], now=T0, alerts_db=adb)
    w = env["wdb"]
    assert out["by_rule"]["ticket_about_to_breach"] == _q(w, "SELECT about_to_breach FROM v_kpi_bq02_summary")[0][0] == 3
    assert out["by_rule"]["p1_open_2h"] == _q(w, "SELECT p1_escalations FROM v_kpi_bq02_summary")[0][0]
    assert out["by_rule"]["customer_at_risk"] == _q(w, "SELECT customers_at_risk FROM v_kpi_bq06_summary")[0][0]
    assert out["by_rule"]["unusual_ticket_volume"] == _q(
        w, "SELECT COUNT(*) FROM v_kpi_bq04_ticket_volume WHERE unusual_volume AND year_month = '2026-09'")[0][0]
    assert out["by_rule"]["crm_billing_mismatch"] == _q(
        w, "SELECT COUNT(*) FROM v_kpi_bq13_crm_telecoms WHERE check_result <> 'matched, dates agree'")[0][0]
    assert out["active"] == out["new"] == sum(out["by_rule"].values())
    assert _q(adb, "SELECT MIN(first_raised_at), MAX(first_raised_at) FROM alerts")[0] == (T0, T0)
    assert all(r[0] for r in _q(adb, "SELECT reason FROM alerts"))


def test_repeat_run_keeps_history_without_duplicates(env):
    adb = env["m"]("a2") / "alerts.duckdb"
    first = run(env["s"], now=T0, alerts_db=adb)
    second = run(env["s"], now=T0 + dt.timedelta(hours=1), alerts_db=adb)
    assert second["new"] == second["cleared"] == 0 and second["active"] == first["active"]
    assert _q(adb, "SELECT COUNT(*) FROM alerts")[0][0] == first["active"]
    assert _q(adb, "SELECT DISTINCT first_raised_at, last_seen_at FROM alerts") == [(T0, T0 + dt.timedelta(hours=1))]
    assert _q(adb, "SELECT run_id, status FROM run_log ORDER BY run_id") == [(1, "ok"), (2, "ok")]


def test_cleared_alerts_are_kept_and_can_reopen(env):
    adb = env["m"]("a3") / "alerts.duckdb"
    rules = load_rules()
    run(env["s"], now=T0, rules=rules, alerts_db=adb)
    quiet = [dict(r, where="FALSE") if r["id"] == "customer_at_risk" else r for r in rules]
    out = run(env["s"], now=T0 + dt.timedelta(days=1), rules=quiet, alerts_db=adb)
    n = _q(env["wdb"], "SELECT customers_at_risk FROM v_kpi_bq06_summary")[0][0]
    assert out["cleared"] == n
    assert _q(adb, "SELECT COUNT(*), MIN(cleared_at) FROM alerts WHERE rule_id = 'customer_at_risk' AND status = 'cleared'")[0] == \
        (n, T0 + dt.timedelta(days=1))
    back = run(env["s"], now=T0 + dt.timedelta(days=2), rules=rules, alerts_db=adb)
    assert back["new"] == n
    assert _q(adb, "SELECT DISTINCT status, times_raised, first_raised_at FROM alerts WHERE rule_id = 'customer_at_risk'") == \
        [("active", 2, T0 + dt.timedelta(days=2))]


def test_a_failed_run_is_logged_and_changes_nothing(env):
    adb = env["m"]("a4") / "alerts.duckdb"
    run(env["s"], now=T0, alerts_db=adb)
    before = _q(adb, "SELECT * FROM alerts ORDER BY alert_id")
    broken = [dict(load_rules()[0], view="v_no_such_view")]
    with pytest.raises(Exception):
        run(env["s"], now=T0 + dt.timedelta(hours=1), rules=broken, alerts_db=adb)
    assert _q(adb, "SELECT * FROM alerts ORDER BY alert_id") == before
    status, error = _q(adb, "SELECT status, error FROM run_log ORDER BY run_id DESC LIMIT 1")[0]
    assert status == "failed" and "v_no_such_view" in error


def test_alerts_hold_no_ticket_text_or_staff_names(env):
    adb = env["m"]("a5") / "alerts.duckdb"
    run(env["s"], now=T0, alerts_db=adb)
    cols = {r[0] for r in _q(adb, "SELECT column_name FROM information_schema.columns WHERE table_name = 'alerts'")}
    assert not cols & {"description", "resolution_notes", "agent", "account_manager"}
