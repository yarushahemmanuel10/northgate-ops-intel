"""KPI views (step 4.4, D-031): each returns rows and matches a calculation done here,
independently, from the fact tables. Built with an empty review-decisions file."""
import csv
import datetime as dt
import statistics

import duckdb
import pytest
from fastapi.testclient import TestClient

from generator.config import load_config
from generator.messiness import build as build_raw
from pipeline.ingest.run import ingest
from pipeline.resolve.run import load_config as load_resolution, resolve
from pipeline.settings import load_settings
from pipeline.validate.run import load_rules, validate
from pipeline.warehouse.dedup import run as find_duplicates
from pipeline.warehouse.kpis import load_thresholds
from pipeline.warehouse.load import DECISION_COLUMNS, build, load_config as load_warehouse
from shop_api.app import create_app

TH = load_thresholds()
AS_OF = dt.datetime(2026, 10, 1)
KPI_VIEWS = ["v_kpi_bq01_sla_monthly", "v_kpi_bq02_open_tickets", "v_kpi_bq03_resolution_time",
             "v_kpi_bq04_ticket_volume", "v_kpi_bq05_renewals", "v_kpi_bq07_revenue_monthly",
             "v_kpi_bq08_customer_summary", "v_kpi_bq09_lines_taken", "v_kpi_bq10_delivery_monthly",
             "v_kpi_bq12_kb_usage", "v_kpi_bq12_link_share", "v_kpi_bq13_crm_telecoms", "v_kpi_bq14_sources",
             "v_kpi_bq14_matching", "v_kpi_bq14_findings", "v_kpi_bq16_onboarding", "v_at_risk"]


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    m = tmp_path_factory.mktemp
    raw, truth, staging, validated, resolved, wh = m("raw"), m("truth"), m("staging"), m("validated"), m("resolved"), m("wh")
    clean, *_ = build_raw(load_config(), raw, truth)
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
    import pandas as pd
    clean_tickets = pd.DataFrame(clean["helpdesk"].rows).fillna("").astype(str)
    return {"db": wh / "northgate.duckdb", "truth": truth, "clean_tickets": clean_tickets}



def _rows(db, sql):
    con = duckdb.connect(str(db), read_only=True)
    try:
        cur = con.execute(sql)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        con.close()


def _hours(a, b):
    return (b - a).total_seconds() / 3600


def test_every_kpi_view_returns_rows(env):
    for v in KPI_VIEWS:
        assert _rows(env["db"], f"SELECT COUNT(*) AS n FROM {v}")[0]["n"] > 0, v


def test_bq01_sla_matches_a_hand_calculation(env):
    tickets = _rows(env["db"], "SELECT * FROM fact_ticket WHERE closed_at IS NOT NULL")
    view = _rows(env["db"], "SELECT priority, SUM(response_measured) AS rm, SUM(response_met) AS rmet, "
                            "SUM(resolution_measured) AS sm, SUM(resolution_met) AS smet, SUM(closed_tickets) AS n "
                            "FROM v_kpi_bq01_sla_monthly GROUP BY 1")
    got = {r["priority"]: r for r in view}
    for p in ("P1", "P2", "P3", "P4"):
        mine = [t for t in tickets if t["priority"] == p]
        resp = [t for t in mine if t["response_sla_measurable"] and t["first_responded_at_flag"] is None]
        reso = [t for t in mine if t["closed_at_flag"] is None and t["opened_at"] is not None]
        assert got[p]["rm"] == len(resp) and got[p]["sm"] == len(reso)
        assert got[p]["rmet"] == sum(_hours(t["opened_at"], t["first_responded_at"]) <= TH["sla_targets"]["response_hours"][p] for t in resp)
        assert got[p]["smet"] == sum(_hours(t["opened_at"], t["closed_at"]) <= TH["sla_targets"]["resolution_hours"][p] for t in reso)
    assert sum(r["n"] for r in view) == len(tickets)                     # every closed ticket counted once


def test_bq02_alerts(env):
    rows = _rows(env["db"], "SELECT * FROM v_kpi_bq02_open_tickets")
    open_n = _rows(env["db"], "SELECT COUNT(*) AS n FROM fact_ticket WHERE closed_at IS NULL")[0]["n"]
    assert len(rows) == open_n == 40
    for r in rows:
        target = TH["sla_targets"]["resolution_hours"][r["priority"]]
        age = _hours(r["opened_at"], AS_OF)
        expected = "breached" if age >= target else "about to breach" if age >= 0.5 * target else "within target"
        assert r["alert"] == expected
    counts = {a: sum(r["alert"] == a for r in rows) for a in ("breached", "about to breach")}
    assert counts == {"breached": 37, "about to breach": 3}


def test_bq03_resolution_time(env):
    r = _rows(env["db"], "SELECT * FROM v_kpi_bq03_resolution_time ORDER BY tickets DESC LIMIT 1")[0]
    hrs = [_hours(t["opened_at"], t["closed_at"]) for t in _rows(env["db"],
           f"SELECT opened_at, closed_at FROM fact_ticket WHERE strftime(closed_at, '%Y-%m') = '{r['year_month']}' "
           f"AND priority = '{r['priority']}' AND category = '{r['category']}' AND closed_at_flag IS NULL")]
    assert len(hrs) == r["tickets"]
    assert abs(statistics.mean(hrs) - r["mean_hours"]) < 1e-6 and abs(statistics.median(hrs) - r["median_hours"]) < 1e-6
    assert _rows(env["db"], "SELECT COUNT(*) AS n FROM v_kpi_bq03_resolution_time WHERE year_month IS NULL")[0]["n"] == 0


def test_bq04_volume(env):
    rows = _rows(env["db"], "SELECT * FROM v_kpi_bq04_ticket_volume")
    assert sum(r["tickets"] for r in rows) == _rows(env["db"], "SELECT COUNT(*) AS n FROM fact_ticket WHERE opened_date IS NOT NULL")[0]["n"]
    assert all(r["unusual_volume"] == (r["tickets"] >= 4) for r in rows if not r["is_unknown"])
    assert all(r["unusual_volume"] is None for r in rows if r["is_unknown"]) and any(r["is_unknown"] for r in rows)
    assert sum(bool(r["unusual_volume"]) for r in rows) == 31


def test_bq05_renewals_match_a_filter_by_hand(env):
    lines = _rows(env["db"], "SELECT * FROM fact_contract_line WHERE end_date IS NULL")
    due = {l["line_id"] for l in lines if l["renewal_date"] and dt.date(2026, 10, 1) <= l["renewal_date"] <= dt.date(2026, 12, 29)}
    missing = {l["line_id"] for l in lines if l["renewal_date"] is None}
    view = _rows(env["db"], "SELECT line_id, renewal_status FROM v_kpi_bq05_renewals")
    assert {r["line_id"] for r in view if r["renewal_status"] == "renews in next 90 days"} == due
    assert {r["line_id"] for r in view if r["renewal_status"] == "renewal date missing"} == missing and missing


def test_bq07_and_bq08_add_up_to_v_revenue(env):
    total = _rows(env["db"], "SELECT SUM(revenue) AS v FROM v_revenue")[0]["v"]
    assert abs(_rows(env["db"], "SELECT SUM(revenue) AS v FROM v_kpi_bq07_revenue_monthly")[0]["v"] - total) < 0.01
    assert abs(_rows(env["db"], "SELECT SUM(revenue_total) AS v FROM v_kpi_bq08_customer_summary")[0]["v"] - total) < 0.01
    assert round(_rows(env["db"], "SELECT SUM(revenue_on_unknown) AS v FROM v_kpi_bq07_revenue_monthly")[0]["v"], 2) == 34271.50
    assert _rows(env["db"], "SELECT SUM(tickets) AS n FROM v_kpi_bq08_customer_summary")[0]["n"] == \
        _rows(env["db"], "SELECT COUNT(*) AS n FROM fact_ticket")[0]["n"]


def test_bq09_single_line(env):
    rows = _rows(env["db"], "SELECT * FROM v_kpi_bq09_lines_taken")
    assert all(r["single_line"] == (r["line_count"] == 1) and r["line_count"] == len(r["lines_taken"].split(", ")) for r in rows)
    assert any(r["single_line"] for r in rows)


def test_bq10_missing_delivery_dates_counted_separately(env):
    rows = _rows(env["db"], "SELECT * FROM v_kpi_bq10_delivery_monthly")
    assert all(r["on_time"] + r["late"] == r["delivered_with_date"] for r in rows)
    missing = _rows(env["db"], "SELECT COUNT(*) AS n FROM fact_order WHERE status = 'delivered' AND delivered_date IS NULL")[0]["n"]
    assert sum(r["delivered_date_missing"] for r in rows) == missing > 0


def test_bq12_superseded_links(env):
    r = _rows(env["db"], "SELECT * FROM v_kpi_bq12_kb_usage WHERE status = 'superseded'")
    assert r
    for a in r:
        n = _rows(env["db"], f"SELECT COUNT(*) AS n FROM fact_ticket WHERE kb_article_id = '{a['kb_id']}' "
                             f"AND opened_date >= DATE '{a['superseded_on']}'")[0]["n"]
        assert a["linked_after_superseded"] == n
    share = _rows(env["db"], "SELECT * FROM v_kpi_bq12_link_share")[0]
    assert share["with_article_link"] + share["without_article_link"] == share["tickets"]


def _known(truth):
    with open(truth / "known_issues.csv", encoding="utf-8", newline="") as f:
        f.readline()
        return list(csv.DictReader(f))


def test_bq13_lists_against_the_answer_key(env):
    rows = _rows(env["db"], "SELECT * FROM v_kpi_bq13_crm_telecoms")
    known = [e for e in _known(env["truth"]) if e["qi"] == "QI-11" and e["table"] == "telecoms_contracts"]
    removed = sum(e["op"] == "remove_row" for e in known)
    added = sum(e["op"] == "add_row" for e in known)
    moved = sum(e["op"] == "change" and e["field"] == "end_date" for e in known)
    assert sum(r["check_result"] == "BL2 line in CRM, no telecoms account" for r in rows) == removed == 3
    assert sum(r["check_result"] == "telecoms account, no BL2 line in CRM" for r in rows) == added == 2
    assert sum(1 for r in rows if r["days_difference"] not in (None, 0)) == moved == 5
    flagged = [r for r in rows if r["check_result"] == "dates differ by more than 30 days"]
    assert len(flagged) == 5 and all(abs(r["days_difference"]) > 30 for r in flagged)
    assert min(abs(r["days_difference"]) for r in flagged) == 31          # so any tolerance from 7 to 30 flags the same 5


def test_bq14_sources_reconcile(env):
    rows = _rows(env["db"], "SELECT * FROM v_kpi_bq14_sources")
    assert len(rows) == 8 and all(r["reconciles"] for r in rows)
    m = _rows(env["db"], "SELECT source, SUM(records) AS n FROM v_kpi_bq14_matching GROUP BY 1")
    facts = {"helpdesk": "fact_ticket", "shop": "fact_order", "telecoms bills": "fact_bill",
             "telecoms contracts": "fact_telecoms_contract"}
    for r in m:
        assert r["n"] == _rows(env["db"], f"SELECT COUNT(*) AS n FROM {facts[r['source']]}")[0]["n"]
    f = _rows(env["db"], "SELECT SUM(findings) AS n FROM v_kpi_bq14_findings")[0]["n"]
    assert f == _rows(env["db"], "SELECT COUNT(*) AS n FROM dq_finding")[0]["n"]


def test_bq16_onboarding_matches_a_hand_calculation(env):
    starts = _rows(env["db"], "SELECT customer_id, MIN(contract_start) AS s FROM fact_contract_line GROUP BY 1")
    tickets = _rows(env["db"], "SELECT customer_id, opened_date, priority FROM fact_ticket")
    expected = {}
    for r in starts:
        if dt.date(2025, 4, 1) <= r["s"] <= dt.date(2026, 9, 1):
            mine = [t for t in tickets if t["customer_id"] == r["customer_id"] and t["opened_date"]
                    and r["s"] <= t["opened_date"] < r["s"] + dt.timedelta(days=30)]
            p12 = sum(t["priority"] in ("P1", "P2") for t in mine)
            expected[r["customer_id"]] = len(mine) >= 3 or p12 >= 1
    view = {r["customer_id"]: r["off_track"] for r in _rows(env["db"], "SELECT * FROM v_kpi_bq16_onboarding")}
    assert view == expected and len(view) == 15 and sum(view.values()) == 2


def test_renewals_and_superseded_links_are_explained(env):
    from eval.kpi_checks import renewals_check, superseded_links_check
    r = renewals_check(env["db"], env["truth"])
    assert r["clean"] - r["warehouse"] == len(r["only_clean"]) and not r["only_warehouse"]
    assert r["only_clean"] == r["explained_by_qi08"] == r["listed_as_missing_date"]      # every gap is a removed date
    s = superseded_links_check(env["db"], env["truth"])
    assert s["warehouse"] == s["planted_minus_removed"] and s["same_tickets"]
    assert (s["planted"], s["removed_by_qi08"], s["warehouse"]) == (15, 4, 11)


def test_sla_rates_differ_from_the_generator_only_by_exclusions(env):
    from eval.sla_eval import compare
    r = compare(env["db"], env["clean_tickets"])
    for p, x in r["priorities"].items():
        # the generator's own rule on exactly the tickets the warehouse measured gives the warehouse's rate
        assert abs(x["gen_res_on_wh_tickets"] - x["wh_res"]) < 1e-12, p
        assert abs(x["gen_resp_on_wh_tickets"] - x["wh_resp"]) < 1e-12, p
        assert x["outcome_differs"] == 0, p
        left_out_res = sum(n for (q, _), n in r["reasons_resolution"].items() if q == p)
        left_out_resp = sum(n for (q, _), n in r["reasons_response"].items() if q == p)
        assert x["gen_closed"] - x["wh_res_n"] == left_out_res, p        # every missing ticket has a reason
        assert x["gen_closed"] - x["wh_resp_n"] == left_out_resp, p
    reasons = {reason for (_, reason) in r["reasons_resolution"]} | {reason for (_, reason) in r["reasons_response"]}
    assert reasons <= {"TIME-001", "no valid priority", "closed with no first response"}
