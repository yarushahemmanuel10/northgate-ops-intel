"""Streamlit dashboard (step 4.5, D-032): every page loads with Streamlit's AppTest, shows the
banner and as-of date, labels modelled revenue, and only selects from warehouse views."""
import re
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

from generator.config import load_config
from generator.messiness import build as build_raw
from pipeline.ingest.run import ingest
from pipeline.resolve.run import load_config as load_resolution, resolve
from pipeline.settings import ROOT, load_settings
from pipeline.validate.run import load_rules, validate
from pipeline.warehouse.dedup import run as find_duplicates
from pipeline.warehouse.load import DECISION_COLUMNS, build, load_config as load_warehouse
from shop_api.app import create_app

PAGES = ["overview", "service", "customers", "data_quality"]
DASH = ROOT / "dashboard"


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    m = tmp_path_factory.mktemp
    raw, truth, staging, validated, resolved, wh = m("raw"), m("truth"), m("staging"), m("validated"), m("resolved"), m("wh")
    build_raw(load_config(), raw, truth)
    s = load_settings()
    s = dict(s, staging_dir=staging, shop_api=dict(s["shop_api"], base_url="http://testserver"))
    ingest(s, client=TestClient(create_app(raw / "shop_orders.json"), base_url="http://testserver"),
           raw_dir=raw, staging_dir=staging)
    validate(s, load_rules(), staging, validated)
    resolve(s, load_resolution(), validated, resolved)
    find_duplicates(s, load_warehouse(), validated, wh)
    empty = m("d") / "d.csv"
    empty.write_text(",".join(DECISION_COLUMNS) + "\n", encoding="utf-8")
    build(s, load_warehouse(), validated, resolved, wh, empty)
    return wh / "northgate.duckdb"


@pytest.fixture(scope="module")
def pages(db):
    import os
    os.environ["NORTHGATE_DB"] = str(db)
    out = {p: AppTest.from_file(str(DASH / "pages" / f"{p}.py"), default_timeout=60).run() for p in PAGES}
    yield out
    del os.environ["NORTHGATE_DB"]


def _view(db, sql):
    con = duckdb.connect(str(db), read_only=True)
    try:
        return con.execute(sql).fetchone()
    finally:
        con.close()


def test_every_page_loads_with_banner_as_of_date_and_role_placeholder(pages):
    for name, at in pages.items():
        assert not at.exception, (name, [e.value for e in at.exception])
        assert any("Synthetic data" in w.value for w in at.warning), name
        assert any("30 September 2026" in c.value for c in at.caption), name
        role = at.sidebar.selectbox[0]
        assert role.label == "Role" and role.disabled, name


def test_numbers_on_screen_come_from_the_views(pages, db):
    m = {x.label: x.value for x in pages["service"].metric}
    breached, about = _view(db, "SELECT breached, about_to_breach FROM v_kpi_bq02_summary")
    assert m["About to breach (live risk)"] == str(about) == "3" and m["Breached, still open"] == str(breached)
    m = {x.label: x.value for x in pages["customers"].metric}
    waiting, unknown = _view(db, "SELECT records_waiting, revenue_on_unknown FROM v_kpi_bq14_review_summary")
    assert m["Records waiting in the review queue"] == str(waiting) and m["Revenue on Unknown customer"] == f"£{unknown:,.0f}"
    m = {x.label: x.value for x in pages["overview"].metric}
    assert m["Revenue, data window"] == f"£{_view(db, 'SELECT revenue_total FROM v_dash_revenue_total')[0]:,.0f}"


def test_service_page_keeps_live_risks_first_and_apart(pages):
    at = pages["service"]
    heads = [h.value for h in at.subheader]
    assert heads.index("About to breach: live risks") < heads.index("Breached and still open")
    live, breached = at.dataframe[0].value, at.dataframe[1].value
    assert len(live) == 3 and len(breached) == 37
    assert set(live["Ticket"]).isdisjoint(set(breached["Ticket"]))


def test_modelled_revenue_is_labelled(pages):
    table = pages["overview"].dataframe[0].value
    for _, r in table.iterrows():
        modelled = r["Business line"].split()[0] in ("BL1", "BL3", "BL4")
        assert r["Business line"].endswith("(modelled)") == modelled and (r["Basis"] == "modelled") == modelled


def test_customers_page_says_prioritisation_aid_and_shows_signals(pages):
    at = pages["customers"]
    assert any("prioritisation aid, not a prediction" in i.value for i in at.info)
    risk = at.dataframe[0].value
    assert len(risk) > 0 and risk["Signals"].str.contains("tickets rising").all()


def _code():
    return {p: p.read_text(encoding="utf-8") for p in DASH.rglob("*.py")}


def test_pages_only_select_from_views():
    for path, text in _code().items():
        # every FROM in dashboard SQL names a view (v_...), never a base table
        for table in re.findall(r"\bFROM\s+([a-z_]+)", text):
            assert table.startswith("v_"), f"{path.name} reads {table}"
        assert not re.search(r"\b(SUM|AVG|COUNT|MIN|MAX|MEDIAN)\s*\(", text), f"{path.name} aggregates in SQL"
        assert not re.search(r"\.(sum|mean|groupby|pivot_table|agg|count)\(", text), f"{path.name} computes in pandas"
    assert "read_only=True" in (DASH / "common.py").read_text(encoding="utf-8")


def test_no_ticket_text_or_personal_data_on_the_dashboard():
    banned = ("agent", "account_manager", "description", "resolution_notes", "ticket_text", "reviewer", "email")
    for path, text in _code().items():
        for b in banned:
            assert b not in text.lower(), f"{path.name} mentions {b}"


def test_dashboard_fixes_from_review(pages):
    # 1. onboarding shows a date, not a timestamp
    assert 'DateColumn(format="D MMM YYYY")' in (DASH / "pages" / "customers.py").read_text(encoding="utf-8")
    # 2. monthly SLA points need at least 3 measured tickets; the counts are shown alongside
    service = (DASH / "pages" / "service.py").read_text(encoding="utf-8")
    assert "resolution_measured >= 3" in service and "Tickets behind each month" in service
    # 3. the at-risk table keeps to the six key columns, so it fits without scrolling sideways
    risk = pages["customers"].dataframe[0].value
    assert list(risk.columns) == ["Rank", "Customer", "Name", "Signals", "Tickets Apr-Jun", "Tickets Jul-Sep"]
    # 4. matching statuses in plain words
    match = next(d.value for d in pages["data_quality"].dataframe if "Match" in d.value.columns)
    assert set(match["Match"]) <= {"Matched automatically", "Decided in review", "Waiting for review", "Not matched"}
    assert "Waiting for review" in set(match["Match"]) and "human_review" not in set(match["Match"])
