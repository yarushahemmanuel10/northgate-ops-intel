"""Duplicate marking (QI-04, step 3.2): the written rules, scored against the answer key."""
import csv
import filecmp

import pytest
from fastapi.testclient import TestClient

from eval.dedup_eval import answer_key, genuine_repeats, score_orders
from generator.config import load_config
from generator.messiness import build as build_raw
from pipeline.ingest.run import ingest
from pipeline.settings import load_settings
from pipeline.validate.run import load_rules, validate
from pipeline.warehouse.dedup import bill_duplicates, load_config as load_warehouse, order_duplicates, run
from shop_api.app import create_app

BILL_COLS = load_warehouse()["duplicates"]["bills"]["compare"]


def _read(p):
    with open(p, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module")
def dirs(tmp_path_factory):
    m = tmp_path_factory.mktemp
    raw, truth, staging, validated, wh, wh2 = m("raw"), m("truth"), m("staging"), m("validated"), m("wh"), m("wh2")
    build_raw(load_config(), raw, truth)
    s = load_settings()
    s = dict(s, staging_dir=staging, shop_api=dict(s["shop_api"], base_url="http://testserver"))
    ingest(s, client=TestClient(create_app(raw / "shop_orders.json"), base_url="http://testserver"),
           raw_dir=raw, staging_dir=staging)
    validate(s, load_rules(), staging, validated)
    run(s, load_warehouse(), validated, wh)
    run(s, load_warehouse(), validated, wh2)
    return {"truth": truth, "validated": validated, "wh": wh, "wh2": wh2}


# ---- the rules, on hand-made records ------------------------------------------------------

def _order(oid, when, status="delivered", total="100.00", cust="1001"):
    return {"order_id": oid, "customer_id": cust, "order_date__value": when, "status": status,
            "order_total__value": total, "_source_row": oid}


def _line(oid, sku="MSE-WL", qty="1.00", price="100.00"):
    return {"order_id": oid, "sku": sku, "quantity__value": qty, "unit_price__value": price}


def test_double_submission_is_marked_against_the_earliest_order():
    orders = [_order("SO-000002", "2026-01-05 10:03"), _order("SO-000001", "2026-01-05 10:00")]
    d = order_duplicates(orders, [_line("SO-000001"), _line("SO-000002")], 30)
    assert [(x["record_key"], x["duplicate_of"]) for x in d] == [("SO-000002", "SO-000001")]


def test_different_status_is_never_a_duplicate():
    orders = [_order("SO-000001", "2026-01-05 10:00"), _order("SO-000002", "2026-01-05 10:01", status="cancelled")]
    assert order_duplicates(orders, [_line("SO-000001"), _line("SO-000002")], 30) == []


def test_repeat_outside_the_window_is_kept():
    orders = [_order("SO-000001", "2026-01-05 10:00"), _order("SO-000002", "2026-01-05 10:31")]
    assert order_duplicates(orders, [_line("SO-000001"), _line("SO-000002")], 30) == []


def test_different_lines_or_customer_are_not_duplicates():
    orders = [_order("SO-000001", "2026-01-05 10:00"), _order("SO-000002", "2026-01-05 10:01"),
              _order("SO-000003", "2026-01-05 10:01", cust="1002")]
    lines = [_line("SO-000001"), _line("SO-000002", qty="2.00", price="50.00"), _line("SO-000003")]
    assert order_duplicates(orders, lines, 30) == []


def test_bill_duplicate_needs_the_whole_row_including_the_id():
    row = {"bill_id": "BILL-000001", "account_no": "TEL-50001", "bill_month": "2026-01", "bill_date": "2026-01-31",
           "line_rental": "10.00", "call_charges": "5.00", "total": "15.00"}
    bills = [dict(row, _source_row="1"), dict(row, _source_row="2"), dict(row, bill_id="BILL-000002", _source_row="3")]
    d = bill_duplicates(bills, BILL_COLS)
    assert [(x["source_row"], x["duplicate_of_row"]) for x in d] == [("2", "1")]


# ---- against the answer key ---------------------------------------------------------------

def test_order_duplicates_against_answer_key(dirs):
    key = answer_key(dirs["truth"])
    v = dirs["validated"]
    orders, lines = _read(v / "shop_orders.csv"), _read(v / "shop_order_lines.csv")
    marked = order_duplicates(orders, lines, load_warehouse()["duplicates"]["orders"]["window_minutes"])
    s = score_orders(marked, key, genuine_repeats(orders, lines, key))
    assert s["precision"] == 1.0 and s["recall"] == 1.0
    assert s["kept_copy_right"] == s["tp"]
    assert s["genuine_repeats_marked"] == 0


def test_no_qi10_row_is_marked_as_duplicate(dirs):
    key = answer_key(dirs["truth"])
    marked = {r["record_key"] for r in _read(dirs["wh"] / "duplicates.csv")}
    assert key["qi10"] and not marked & key["qi10"]


def test_bill_duplicates_against_answer_key(dirs):
    key = answer_key(dirs["truth"])
    marked = {r["record_key"] for r in _read(dirs["wh"] / "duplicates.csv") if r["table"] == "telecoms_bills"}
    assert marked == key["bills"]


def test_nothing_is_deleted_and_output_is_deterministic(dirs):
    for name in ("shop_orders.csv", "telecoms_bills.csv"):
        assert len(_read(dirs["validated"] / name)) > 0                       # validated rows untouched
    assert filecmp.cmp(dirs["wh"] / "duplicates.csv", dirs["wh2"] / "duplicates.csv", shallow=False)
