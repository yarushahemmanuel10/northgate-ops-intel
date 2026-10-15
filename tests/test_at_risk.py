"""At-risk view (step 4.1, D-028): thresholds match the documented pattern, the view applies
the rule exactly and shows its signals, and the answer key is found before and after review."""
import csv
from decimal import Decimal

import duckdb
import pytest
import yaml
from fastapi.testclient import TestClient

from eval.at_risk_eval import correct_decisions, flagged, score, truth_at_risk
from generator.config import load_config
from generator.messiness import build as build_raw
from pipeline.ingest.run import ingest
from pipeline.resolve.run import load_config as load_resolution, resolve
from pipeline.settings import ROOT, load_settings
from pipeline.validate.run import load_rules, validate
from pipeline.warehouse.dedup import run as find_duplicates
from pipeline.warehouse.load import DECISION_COLUMNS, build, load_at_risk, load_config as load_warehouse
from shop_api.app import create_app


def test_thresholds_match_the_generator_config():
    gen = yaml.safe_load((ROOT / "config" / "generator.yaml").read_text(encoding="utf-8"))
    risk = load_at_risk()
    assert risk["ticket_rise"]["ratio"] == gen["tickets"]["at_risk_rise"]["ratio"] == 2.0
    assert risk["ticket_rise"]["min_last_quarter"] == gen["tickets"]["at_risk_rise"]["min_last_quarter"] == 8
    assert risk["bl2_fall"]["max_ratio"] == gen["telecoms"]["at_risk_fall"]["max_ratio"] == 0.85
    assert risk["shop_fall"]["max_ratio"] == gen["shop"]["at_risk_fall"]["max_ratio"] == 0.4
    assert risk["quarters"] == {"previous": {"start": "2026-04-01", "end": "2026-06-30"},
                                "last": {"start": "2026-07-01", "end": "2026-09-30"}}    # fact sheet §1.7


@pytest.fixture(scope="module")
def env(tmp_path_factory):
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
    empty = m("d0") / "d.csv"
    empty.write_text(",".join(DECISION_COLUMNS) + "\n", encoding="utf-8")
    build(s, load_warehouse(), validated, resolved, wh, empty)
    return {"s": s, "truth": truth, "validated": validated, "resolved": resolved, "wh": wh,
            "db": wh / "northgate.duckdb", "m": m}


def _rows(db, sql):
    con = duckdb.connect(str(db), read_only=True)
    try:
        cur = con.execute(sql)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        con.close()


def test_view_applies_the_rule_exactly(env):
    risk = load_at_risk()
    rows = _rows(env["db"], "SELECT * FROM v_at_risk")
    assert len(rows) == _rows(env["db"], "SELECT COUNT(*) AS n FROM dim_customer WHERE NOT is_unknown")[0]["n"]
    for r in rows:
        rise = r["tickets_last"] >= risk["ticket_rise"]["ratio"] * r["tickets_prev"] and \
            r["tickets_last"] >= risk["ticket_rise"]["min_last_quarter"]
        bl2 = r["bl2_prev"] > 0 and r["bl2_last"] <= Decimal(str(risk["bl2_fall"]["max_ratio"])) * r["bl2_prev"]
        shop = r["shop_prev"] > 0 and r["shop_last"] <= Decimal(str(risk["shop_fall"]["max_ratio"])) * r["shop_prev"]
        assert (r["ticket_rise"], r["bl2_fall"], r["shop_fall"]) == (rise, bl2, shop), r["customer_id"]
        assert r["at_risk"] == (r["status"] == "active" and rise and (bl2 or shop))
        assert r["at_risk_score"] == rise + bl2 + shop
        fired = [n for n, on in (("tickets rising", rise), ("BL2 spend falling", bl2), ("shop spend falling", shop)) if on]
        assert r["signals_fired"] == "; ".join(fired)


def test_counts_come_from_the_facts(env):
    r = _rows(env["db"], "SELECT * FROM v_at_risk WHERE customer_id = 'ACC-0020'")[0]
    t = _rows(env["db"], "SELECT COUNT(*) AS n FROM fact_ticket WHERE customer_id = 'ACC-0020' "
                         "AND opened_date BETWEEN DATE '2026-07-01' AND DATE '2026-09-30'")[0]["n"]
    s = _rows(env["db"], "SELECT SUM(order_total) AS v FROM fact_order WHERE customer_id = 'ACC-0020' "
                         "AND revenue_treatment = 'include' AND order_date BETWEEN DATE '2026-04-01' AND DATE '2026-06-30'")[0]["v"]
    assert r["tickets_last"] == t and r["shop_prev"] == s


def test_flagged_customers_show_at_least_two_signals_and_rank_first(env):
    rows = _rows(env["db"], "SELECT * FROM v_at_risk ORDER BY retention_rank")
    assert [r["retention_rank"] for r in rows] == list(range(1, len(rows) + 1))
    n = sum(r["at_risk"] for r in rows)
    assert n > 0 and all(r["at_risk"] for r in rows[:n]) and not any(r["at_risk"] for r in rows[n:])
    assert all(r["at_risk_score"] >= 2 and "tickets rising" in r["signals_fired"] for r in rows[:n])
    for a, b in zip(rows[:n], rows[1:n]):
        assert (a["at_risk_score"], a["annual_contract_value"]) >= (b["at_risk_score"], b["annual_contract_value"])
    assert not any(r["customer_id"] == "UNKNOWN" for r in rows)
    assert not any(r["at_risk"] for r in rows if r["status"] != "active")


def test_answer_key_before_and_after_review(env):
    risk = truth_at_risk(env["truth"])
    before = score(flagged(env["db"]), risk)
    assert len(before["found"]) == 9 and before["missed"] == ["ACC-0109"] and before["healthy_flagged"] == ["ACC-0076"]
    d = env["m"]("after")
    with open(d / "decisions.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=DECISION_COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(correct_decisions(env["resolved"], env["validated"], env["truth"]))
    (d / "duplicates.csv").write_bytes((env["wh"] / "duplicates.csv").read_bytes())
    build(env["s"], load_warehouse(), env["validated"], env["resolved"], d, d / "decisions.csv")
    after = score(flagged(d / "northgate.duckdb"), risk)
    assert len(after["found"]) == 10 and after["missed"] == [] and after["healthy_flagged"] == ["ACC-0076"]
