"""Layer 2 star schema (step 3.3, D-025): integrity, row counts, flags, the Unknown customer and
human review decisions. The tests may read the answer key; pipeline code may not."""
import csv
import datetime as dt

import duckdb
import pytest
from fastapi.testclient import TestClient

from generator.config import load_config
from generator.messiness import build as build_raw
from pipeline.ingest.run import ingest
from pipeline.resolve.run import load_config as load_resolution, resolve
from pipeline.settings import ROOT, load_settings
from pipeline.validate.run import load_rules, validate
from pipeline.warehouse.dedup import run as find_duplicates
from pipeline.warehouse.load import DATE_COLUMNS, DECISION_COLUMNS, build, load_config as load_warehouse
from shop_api.app import create_app

FACTS = ["fact_ticket", "fact_order", "fact_order_line", "fact_bill", "fact_contract_line"]


def _read(p, skip_marker=False):
    with open(p, encoding="utf-8", newline="") as f:
        if skip_marker:
            f.readline()
        return list(csv.DictReader(f))


def _q(db, sql, params=None):
    con = duckdb.connect(str(db), read_only=True)
    try:
        return con.execute(sql, params or {}).fetchall()
    finally:
        con.close()


@pytest.fixture(scope="module")
def dirs(tmp_path_factory):
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
    out = build(s, load_warehouse(), validated, resolved, wh, empty)
    return {"s": s, "truth": truth, "validated": validated, "resolved": resolved, "wh": wh,
            "db": wh / "northgate.duckdb", "out": out, "tmp": m}


# ---- integrity ---------------------------------------------------------------------------

def test_every_fact_link_exists(dirs):
    for fact in FACTS:
        assert _q(dirs["db"], f"SELECT COUNT(*) FROM {fact} f ANTI JOIN dim_customer d USING (customer_id)")[0][0] == 0, fact
        assert _q(dirs["db"], f"SELECT COUNT(*) FROM {fact} f ANTI JOIN dim_business_line b USING (business_line)")[0][0] == 0, fact
        assert _q(dirs["db"], f"SELECT COUNT(*) FROM {fact} WHERE customer_id IS NULL OR business_line IS NULL")[0][0] == 0
    assert _q(dirs["db"], "SELECT COUNT(*) FROM ticket_text t ANTI JOIN fact_ticket f USING (ticket_id)")[0][0] == 0
    assert _q(dirs["db"], "SELECT COUNT(*) FROM fact_order_line l ANTI JOIN fact_order o USING (order_id)")[0][0] == 0


def test_dim_date_covers_every_date(dirs):
    for table, cols in DATE_COLUMNS.items():
        for c in cols:
            missing = _q(dirs["db"], f"SELECT COUNT(*) FROM {table} t WHERE {c} IS NOT NULL "
                                     f"AND {c} NOT IN (SELECT date FROM dim_date)")[0][0]
            assert missing == 0, f"{table}.{c}"
    lo, hi, n = _q(dirs["db"], "SELECT MIN(date), MAX(date), COUNT(*) FROM dim_date")[0]
    assert n == (hi - lo).days + 1                                         # no gaps
    as_of = dt.date.fromisoformat(dirs["s"]["as_of_moment"][:10])
    assert _q(dirs["db"], "SELECT MAX(renewal_date) FROM fact_contract_line")[0][0] > as_of
    assert _q(dirs["db"], "SELECT COUNT(*) FROM dim_date WHERE after_as_of")[0][0] > 0


def test_fact_rows_are_validated_rows_minus_marked_duplicates(dirs):
    v, dups = dirs["validated"], _read(dirs["wh"] / "duplicates.csv")
    dup_orders = {d["record_key"] for d in dups if d["table"] == "shop_orders"}
    dup_bills = sum(d["table"] == "telecoms_bills" for d in dups)
    assert dup_orders and dup_bills
    lines = _read(v / "shop_order_lines.csv")
    expected = {"fact_ticket": len(_read(v / "helpdesk_tickets.csv")),
                "ticket_text": len(_read(v / "helpdesk_tickets.csv")),
                "fact_order": len(_read(v / "shop_orders.csv")) - len(dup_orders),
                "fact_order_line": len(lines) - sum(ln["order_id"] in dup_orders for ln in lines),
                "fact_bill": len(_read(v / "telecoms_bills.csv")) - dup_bills,
                "fact_contract_line": len(_read(v / "crm_contract_lines.csv"))}
    for table, n in expected.items():
        assert _q(dirs["db"], f"SELECT COUNT(*) FROM {table}")[0][0] == n, table
    assert not _q(dirs["db"], f"SELECT order_id FROM fact_order WHERE order_id IN {tuple(sorted(dup_orders))}")


def test_ticket_text_kept_out_of_fact_ticket(dirs):
    cols = {r[0] for r in _q(dirs["db"], "SELECT column_name FROM information_schema.columns WHERE table_name = 'fact_ticket'")}
    assert not cols & {"description", "resolution_notes"}


# ---- typed values and flags --------------------------------------------------------------

def test_invalid_values_are_empty_with_a_reason(dirs):
    db = dirs["db"]
    assert _q(db, "SELECT COUNT(*) FROM fact_ticket WHERE priority_flag LIKE 'PRIORITY-002%'")[0][0] > 0
    assert _q(db, "SELECT COUNT(*) FROM fact_ticket WHERE priority_flag LIKE 'PRIORITY-002%' AND priority IS NOT NULL")[0][0] == 0
    assert _q(db, "SELECT COUNT(*) FROM fact_ticket WHERE priority IS NULL AND priority_flag IS NULL")[0][0] == 0
    assert _q(db, "SELECT COUNT(*) FROM fact_order_line WHERE quantity_flag LIKE 'QTY-001%'")[0][0] > 0
    assert _q(db, "SELECT COUNT(*) FROM fact_order_line WHERE quantity_flag LIKE 'QTY-001%' AND quantity IS NOT NULL")[0][0] == 0
    assert _q(db, "SELECT COUNT(*) FROM fact_order_line WHERE quantity <= 0")[0][0] == 0
    assert _q(db, "SELECT COUNT(*) FROM fact_ticket WHERE NOT response_sla_measurable AND response_sla_flag IS NULL")[0][0] == 0
    assert _q(db, "SELECT COUNT(*) FROM fact_ticket WHERE response_sla_measurable AND response_sla_flag IS NOT NULL")[0][0] == 0
    assert _q(db, "SELECT COUNT(*) FROM fact_contract_line WHERE renewal_date_flag LIKE 'MISSING-003%' AND renewal_date IS NULL")[0][0] > 0


def test_consistency_findings_keep_the_value(dirs):
    db = dirs["db"]
    rows = _q(db, "SELECT closed_at, opened_at FROM fact_ticket WHERE closed_at_flag LIKE 'TIME-001%'")
    assert rows and all(c is not None and c < o for c, o in rows)          # kept, flagged, excluded on purpose later
    assert _q(db, "SELECT COUNT(*) FROM fact_order WHERE order_total_flag LIKE 'ARITH-001%' AND order_total IS NULL")[0][0] == 0


def test_typed_columns_are_typed(dirs):
    types = dict(_q(dirs["db"], "SELECT table_name || '.' || column_name, data_type FROM information_schema.columns"))
    assert types["fact_ticket.opened_at"] == "TIMESTAMP" and types["fact_order.order_total"].startswith("DECIMAL")
    assert types["fact_bill.bill_month"] == "DATE" and types["fact_order_line.quantity"] == "INTEGER"


# ---- Unknown customer and review decisions -----------------------------------------------

def test_queued_and_unmatched_records_link_to_unknown(dirs):
    db = dirs["db"]
    for fact in ("fact_ticket", "fact_order", "fact_bill"):
        assert _q(db, f"SELECT COUNT(*) FROM {fact} WHERE match_status IN ('review', 'unmatched') AND customer_id <> 'UNKNOWN'")[0][0] == 0
        assert _q(db, f"SELECT COUNT(*) FROM {fact} WHERE match_status = 'auto' AND customer_id = 'UNKNOWN'")[0][0] == 0
    cw = _read(dirs["resolved"] / "crosswalk.csv")
    dup_orders = {d["record_key"] for d in _read(dirs["wh"] / "duplicates.csv") if d["table"] == "shop_orders"}
    queued_tickets = sum(r["source_table"] == "helpdesk_tickets" and r["status"] != "auto" for r in cw)
    queued_orders = sum(r["source_table"] == "shop_orders" and r["status"] != "auto" and r["source_key"] not in dup_orders for r in cw)
    assert _q(db, "SELECT COUNT(*) FROM fact_ticket WHERE customer_id = 'UNKNOWN'")[0][0] == queued_tickets
    assert _q(db, "SELECT COUNT(*) FROM fact_order WHERE customer_id = 'UNKNOWN'")[0][0] == queued_orders
    assert dirs["out"]["decisions_applied"] == 0


def _decisions(path, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=DECISION_COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return path


def test_sample_decision_moves_records_to_the_right_customer(dirs):
    queue = _read(dirs["resolved"] / "review_queue.csv")
    unit = next(q for q in queue if q["source_system"] == "shop")
    shop_id = unit["unit"].split()[-1]
    truth = {r["shop_customer_id"]: r["account_id"] for r in _read(dirs["truth"] / "shop_customers.csv", True)}
    right = truth[shop_id]
    ticket = next(r for r in _read(dirs["resolved"] / "crosswalk.csv")
                  if r["source_table"] == "helpdesk_tickets" and r["status"] == "review")
    ticket_truth = {r["ticket_id"]: r["account_id"] for r in _read(dirs["truth"] / "tickets.csv", True)}[ticket["source_key"]]
    path = _decisions(dirs["tmp"]("d1") / "d.csv", [
        {"source_system": "shop", "source_key": unit["unit"], "decided_account_id": right, "reviewer": "Test Reviewer",
         "decided_on": "2026-10-15", "reason": "confirmed with the account manager"},
        {"source_system": "helpdesk", "source_key": ticket["source_key"], "decided_account_id": ticket_truth,
         "reviewer": "Test Reviewer", "decided_on": "2026-10-15", "reason": "caller named the company"}])
    wh = dirs["tmp"]("wh_decided")
    (wh / "duplicates.csv").write_bytes((dirs["wh"] / "duplicates.csv").read_bytes())
    out = build(dirs["s"], load_warehouse(), dirs["validated"], dirs["resolved"], wh, path)
    db = wh / "northgate.duckdb"
    rows = _q(db, "SELECT DISTINCT customer_id, match_status, match_method FROM fact_order WHERE shop_customer_id = $s", {"s": shop_id})
    assert rows == [(right, "human_review", "human_review")]
    assert _q(db, "SELECT customer_id, match_method FROM fact_ticket WHERE ticket_id = $t", {"t": ticket["source_key"]}) == \
        [(ticket_truth, "human_review")]
    assert out["decisions_applied"] == int(unit["records"]) + 1
    before = _q(dirs["db"], "SELECT COUNT(*) FROM fact_order WHERE customer_id = 'UNKNOWN'")[0][0]
    after = _q(db, "SELECT COUNT(*) FROM fact_order WHERE customer_id = 'UNKNOWN'")[0][0]
    assert before - after == _q(db, "SELECT COUNT(*) FROM fact_order WHERE shop_customer_id = $s", {"s": shop_id})[0][0]


@pytest.mark.parametrize("change, message", [
    ({"decided_account_id": "ACC-9999"}, "not a canonical customer"),
    ({"source_key": "no such name"}, "no helpdesk record"),
    ({"reviewer": ""}, "missing"),
])
def test_bad_decisions_are_refused(dirs, change, message):
    ticket = next(r for r in _read(dirs["resolved"] / "crosswalk.csv")
                  if r["source_table"] == "helpdesk_tickets" and r["status"] == "review")
    row = {"source_system": "helpdesk", "source_key": ticket["source_key"], "decided_account_id": "ACC-0001",
           "reviewer": "Test Reviewer", "decided_on": "2026-10-15", "reason": "test"}
    path = _decisions(dirs["tmp"]("bad") / "d.csv", [dict(row, **change)])
    with pytest.raises(ValueError, match=message):
        _build_with_dups(dirs, path)


def test_decision_for_an_automatic_match_is_refused(dirs):
    auto = next(r for r in _read(dirs["resolved"] / "crosswalk.csv")
                if r["source_table"] == "helpdesk_tickets" and r["status"] == "auto")
    path = _decisions(dirs["tmp"]("auto") / "d.csv", [
        {"source_system": "helpdesk", "source_key": auto["source_key"], "decided_account_id": "ACC-0001",
         "reviewer": "Test Reviewer", "decided_on": "2026-10-15", "reason": "test"}])
    with pytest.raises(ValueError, match="not in the review queue"):
        _build_with_dups(dirs, path)


def _build_with_dups(dirs, path):
    wh = dirs["tmp"]("whx")
    (wh / "duplicates.csv").write_bytes((dirs["wh"] / "duplicates.csv").read_bytes())
    return build(dirs["s"], load_warehouse(), dirs["validated"], dirs["resolved"], wh, path)


def test_committed_review_decisions_file_is_empty_with_header():
    rows = (ROOT / "config" / "review_decisions.csv").read_text(encoding="utf-8").splitlines()
    assert rows == [",".join(DECISION_COLUMNS)]


# ---- no answer-key fields ---------------------------------------------------------------

def test_dim_customer_holds_only_crm_fields(dirs):
    cols = [r[0] for r in _q(dirs["db"], "SELECT column_name FROM information_schema.columns "
                                         "WHERE table_name = 'dim_customer' ORDER BY ordinal_position")]
    assert cols == ["customer_id", "company_name", "sector", "account_manager", "customer_since", "status",
                    "closed_date", "merged_crm_records", "is_unknown"]
    # generator answer-key fields never appear anywhere in the warehouse
    cols = _q(dirs["db"], "SELECT table_name, lower(column_name) FROM information_schema.columns")
    for table, col in cols:
        for banned in ("truth", "look_alike", "lookalike", "planted", "pattern", "switch", "is_new_in_window",
                       "shop_annual_budget", "business_lines"):
            assert banned not in col, f"{table}.{col}"
        # "risk" only in v_at_risk, which computes it from pipeline data (D-028); never in a base table
        if "risk" in col:
            assert table == "v_at_risk", f"{table}.{col}"
    base = {r[0] for r in _q(dirs["db"], "SELECT table_name FROM information_schema.tables WHERE table_type = 'BASE TABLE'")}
    assert "v_at_risk" not in base and "dim_customer" in base
