"""Revenue views (step 3.4, D-026): defined as in semantic/metrics.yaml, credit notes added as
they are, Unknown customer included, modelled revenue labelled, and every line reconciled."""
import datetime as dt
import shutil

import duckdb
import pytest
from fastapi.testclient import TestClient

from eval.reconciliation import CLEANED, generator_modelled, warehouse_revenue
from generator.config import load_config
from generator.messiness import build as build_raw
from pipeline.ingest.run import ingest
from pipeline.resolve.run import load_config as load_resolution, resolve
from pipeline.settings import load_settings
from pipeline.validate.run import load_rules, validate
from pipeline.warehouse.dedup import run as find_duplicates
from pipeline.warehouse.load import DECISION_COLUMNS, build, load_config as load_warehouse
from shop_api.app import create_app


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    m = tmp_path_factory.mktemp
    raw, truth, staging, validated, resolved, wh = m("raw"), m("truth"), m("staging"), m("validated"), m("resolved"), m("wh")
    build_raw(load_config(), raw, truth)
    s = load_settings()
    s = dict(s, shop_api=dict(s["shop_api"], base_url="http://testserver"))
    ingest(s, client=TestClient(create_app(raw / "shop_orders.json"), base_url="http://testserver"),
           raw_dir=raw, staging_dir=staging)
    validate(s, load_rules(), staging, validated)
    resolve(s, load_resolution(), validated, resolved)
    find_duplicates(s, load_warehouse(), validated, wh)
    empty = m("decisions") / "review_decisions.csv"
    empty.write_text(",".join(DECISION_COLUMNS) + "\n", encoding="utf-8")
    build(s, load_warehouse(), validated, resolved, wh, empty)
    return wh / "northgate.duckdb"


def _q(path, sql, read_only=True):
    con = duckdb.connect(str(path), read_only=read_only)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def test_bl2_adds_credit_notes_as_they_are(db):
    bl2 = _q(db, "SELECT SUM(revenue) FROM v_revenue WHERE business_line = 'BL2'")[0][0]
    bills = _q(db, "SELECT SUM(total) FROM fact_bill WHERE revenue_treatment <> 'exclude'")[0][0]
    include, credits = _q(db, "SELECT SUM(total) FILTER (WHERE revenue_treatment = 'include'), "
                              "SUM(total) FILTER (WHERE revenue_treatment = 'negative') FROM fact_bill")[0]
    assert round(bl2, 2) == float(bills) == 1072853.46
    assert credits < 0 and round(float(credits), 2) == -2419.37
    assert round(bl2, 2) == round(float(include + credits), 2)              # added, not subtracted again
    assert round(bl2, 2) != round(float(include - credits), 2)


def test_bl5_is_included_order_totals(db):
    bl5 = _q(db, "SELECT SUM(revenue) FROM v_revenue WHERE business_line = 'BL5'")[0][0]
    orders = _q(db, "SELECT SUM(order_total) FROM fact_order WHERE revenue_treatment = 'include'")[0][0]
    assert round(bl5, 2) == float(orders) == 734394.00
    assert _q(db, "SELECT COUNT(*) FROM v_revenue WHERE business_line = 'BL5' AND records = 0")[0][0] == 0


def test_unknown_customer_is_in_the_totals(db):
    unknown = _q(db, "SELECT SUM(revenue) FROM v_revenue WHERE customer_id = 'UNKNOWN' AND business_line = 'BL5'")[0][0]
    facts = _q(db, "SELECT SUM(order_total) FROM fact_order WHERE customer_id = 'UNKNOWN' AND revenue_treatment = 'include'")[0][0]
    assert round(unknown, 2) == float(facts) == 34271.50
    w = warehouse_revenue(db)
    assert round(w["all"]["BL5"] - w["known"]["BL5"], 2) == 34271.50


def test_every_row_says_modelled_or_measured(db):
    rows = dict(_q(db, "SELECT DISTINCT business_line, revenue_basis FROM v_revenue"))
    assert rows == {"BL1": "modelled", "BL2": "measured", "BL3": "modelled", "BL4": "modelled", "BL5": "measured"}
    assert _q(db, "SELECT COUNT(*) FROM v_revenue v JOIN dim_business_line b USING (business_line) "
                  "WHERE (v.revenue_basis = 'modelled') <> b.modelled")[0][0] == 0


def test_active_months_follow_the_d007_rule(db):
    window = [dt.date(2025, 4, 1)]
    while window[-1] < dt.date(2026, 9, 1):
        d = window[-1]
        window.append(dt.date(d.year + d.month // 12, d.month % 12 + 1, 1))
    lines = _q(db, "SELECT line_id, contract_start, end_date FROM fact_contract_line c JOIN dim_business_line b "
                   "USING (business_line) WHERE b.modelled")
    got = dict(_q(db, "SELECT line_id, COUNT(*) FROM v_contract_revenue_month GROUP BY 1"))
    mid_month_end = 0
    for line_id, start, end in lines:
        def month_end(m):
            return dt.date(m.year + m.month // 12, m.month % 12 + 1, 1) - dt.timedelta(days=1)
        expected = sum(1 for m in window if start <= month_end(m) and (end is None or end >= m))
        assert got.get(line_id, 0) == expected, line_id
        if end and end.day != 1 and dt.date(2025, 4, 1) <= end <= dt.date(2026, 9, 30):
            mid_month_end += 1
            months = {r[0] for r in _q(db, f"SELECT month FROM v_contract_revenue_month WHERE line_id = '{line_id}'")}
            assert end.replace(day=1) in months                              # the end month counts in full
    assert mid_month_end > 0


def test_revenue_months_inside_the_window(db):
    lo, hi = _q(db, "SELECT MIN(month), MAX(month) FROM v_revenue")[0]
    assert lo >= dt.date(2025, 4, 1) and hi <= dt.date(2026, 9, 1)


def test_unusable_value_is_reported_not_silently_dropped(db, tmp_path):
    copy = tmp_path / "copy.duckdb"
    shutil.copy(db, copy)
    line, before = _q(copy, "SELECT line_id, SUM(revenue) FROM v_contract_revenue_month "
                            "WHERE business_line = 'BL1' GROUP BY 1 ORDER BY 1 LIMIT 1")[0]
    total = _q(copy, "SELECT SUM(revenue) FROM v_revenue WHERE business_line = 'BL1'")[0][0]
    con = duckdb.connect(str(copy))
    con.execute(f"UPDATE fact_contract_line SET annual_value = NULL WHERE line_id = '{line}'")
    con.close()
    assert _q(copy, "SELECT business_line, records FROM v_revenue_excluded") == [("BL1", 1)]
    after = _q(copy, "SELECT SUM(revenue) FROM v_revenue WHERE business_line = 'BL1'")[0][0]
    assert round(total - after, 2) == round(before, 2)
    assert _q(db, "SELECT COUNT(*) FROM v_revenue_excluded")[0][0] == 0      # nothing lost on the real data


def test_all_five_lines_reconcile(db):
    w = warehouse_revenue(db)["all"]
    for bl, (reported, exact) in generator_modelled().items():
        assert abs(w[bl] - exact) < 0.01, bl                                  # same rule, to the penny
        assert abs(w[bl] - reported) < 0.5, bl                                # generator reports whole pounds
    for bl, ref in CLEANED.items():
        assert abs(w[bl] - ref) < 0.5, bl
