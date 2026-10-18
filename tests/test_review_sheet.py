"""Review sheet and applying human decisions (step 4.2, D-029)."""
import csv
import re

import duckdb
import pytest
from fastapi.testclient import TestClient

from generator.config import load_config
from generator.messiness import build as build_raw
from pipeline import run as pipeline_run
from pipeline.settings import load_settings
from pipeline.warehouse import review_sheet
from pipeline.warehouse.apply_review import apply
from pipeline.warehouse.load import DECISION_COLUMNS, build, load_config as load_warehouse
from shop_api.app import create_app

DOMAIN = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$")


def _read(p):
    with open(p, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    raw, truth, root = tmp_path_factory.mktemp("raw"), tmp_path_factory.mktemp("truth"), tmp_path_factory.mktemp("run")
    build_raw(load_config(), raw, truth)
    s = load_settings()
    s = dict(s, raw_dir=raw, staging_dir=root / "staging", validated_dir=root / "validated",
             resolved_dir=root / "resolved", warehouse_dir=root / "warehouse", alerts_dir=root / "alerts", retrieval_dir=root / "retrieval",
             shop_api=dict(s["shop_api"], base_url="http://testserver"))
    pipeline_run.run_all(s, client=TestClient(create_app(raw / "shop_orders.json"), base_url="http://testserver"))
    review_sheet.run(s)
    return {"s": s, "sheet": _read(s["warehouse_dir"] / "review_sheet.csv"), "tmp": tmp_path_factory.mktemp}


def _write(path, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else DECISION_COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return path


def _decision(system, key, acc):
    return {"source_system": system, "source_key": key, "decided_account_id": acc, "reviewer": "Test Reviewer",
            "decided_on": "2026-10-15", "reason": "test"}


def _build(env, path):
    wh = env["tmp"]("wh")
    (wh / "duplicates.csv").write_bytes((env["s"]["warehouse_dir"] / "duplicates.csv").read_bytes())
    out = build(env["s"], load_warehouse(), warehouse=wh, decisions_path=path)
    return out, wh / "northgate.duckdb"


def _q(db, sql):
    con = duckdb.connect(str(db), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


# ---- the sheet ---------------------------------------------------------------------------

def test_sheet_lists_every_queue_item_and_leaves_decisions_blank(env):
    sheet, queue = env["sheet"], _read(env["s"]["resolved_dir"] / "review_queue.csv")
    assert list(sheet[0].keys())[:6] == DECISION_COLUMNS
    items = [r for r in sheet if r["level"] == "item"]
    assert sorted(r["source_key"] for r in items) == sorted(q["unit"] for q in queue)
    assert all(r[c] == "" for r in sheet for c in ("decided_account_id", "reviewer", "decided_on", "reason"))


def test_shared_spellings_are_listed_record_by_record(env):
    sheet, queue = env["sheet"], _read(env["s"]["resolved_dir"] / "review_queue.csv")
    for q in queue:
        recs = [r for r in env["sheet"] if r["level"] == "record" and r["queue_item"] == q["unit"]]
        if q["source_system"] == "helpdesk" and q["reason"] == "too close to another candidate":
            assert len(recs) == int(q["records"]) and all(r["source_key"].startswith("INC-") for r in recs)
        else:
            assert recs == []
    rp = [r for r in sheet if r["queue_item"] == "RP Solicitors" and r["level"] == "record"]
    assert rp and {rp[0]["candidate_1"], rp[0]["candidate_2"]} == {"ACC-0098", "ACC-0109"}


def test_evidence_holds_email_domains_only(env):
    domains = [d for r in env["sheet"] for d in r["email_domains"].split("; ") if d]
    assert domains and all(DOMAIN.match(d) and "@" not in d for d in domains)
    text = " ".join(" ".join(r.values()) for r in env["sheet"])
    tickets = {t["ticket_id"]: t for t in _read(env["s"]["validated_dir"] / "helpdesk_tickets.csv")}
    for r in env["sheet"]:
        if r["level"] == "record":
            t = tickets[r["source_key"]]
            assert t["description"] not in text and "@" not in r["email_domains"]
    assert not {"agent", "account_manager", "description", "resolution_notes"} & set(env["sheet"][0].keys())


def test_shop_rows_show_orders_and_names_as_written(env):
    shop = [r for r in env["sheet"] if r["source_system"] == "shop"]
    assert len(shop) == 3
    db = env["s"]["warehouse_dir"] / "northgate.duckdb"
    for r in shop:                                    # same total as the warehouse: duplicates left out
        cid = r["source_key"].split()[-1]
        total = _q(db, f"SELECT SUM(order_total) FROM fact_order WHERE shop_customer_id = '{cid}' "
                       "AND revenue_treatment = 'include'")[0][0]
        assert round(float(r["orders_total"]), 2) == float(total)
    assert all(r["first_date"] and r["last_date"] and float(r["orders_total"]) > 0 and r["names_as_written"] for r in shop)


# ---- decisions ---------------------------------------------------------------------------

def test_blank_decisions_change_nothing(env):
    baseline_path = _write(env["tmp"]("b0") / "d.csv", [])
    _, base = _build(env, baseline_path)
    sheet_as_is = _write(env["tmp"]("b1") / "d.csv", env["sheet"])       # the sheet copied over, nothing filled in
    out, db = _build(env, sheet_as_is)
    assert out["decisions_applied"] == 0
    for table in ("fact_ticket", "fact_order", "fact_bill", "v_revenue", "v_at_risk"):
        assert _q(db, f"SELECT * FROM {table} ORDER BY ALL") == _q(base, f"SELECT * FROM {table} ORDER BY ALL"), table


def test_record_decision_overrides_whole_item(env):
    rp = [r for r in env["sheet"] if r["queue_item"] == "RP Solicitors" and r["level"] == "record"]
    one = rp[0]["source_key"]
    # record-level row first, whole-item row after: the record still wins, whatever the order
    path = _write(env["tmp"]("ov") / "d.csv", [_decision("helpdesk", one, "ACC-0109"),
                                              _decision("helpdesk", "RP Solicitors", "ACC-0098")])
    out, db = _build(env, path)
    got = dict(_q(db, "SELECT ticket_id, customer_id FROM fact_ticket WHERE match_status = 'human_review'"))
    assert got[one] == "ACC-0109"
    assert all(got[r["source_key"]] == "ACC-0098" for r in rp[1:])
    assert out["decisions_applied"] == len(rp)


def test_conflicting_decisions_at_the_same_level_are_refused(env):
    path = _write(env["tmp"]("cf") / "d.csv", [_decision("helpdesk", "RP Solicitors", "ACC-0098"),
                                              _decision("helpdesk", "RP Solicitors", "ACC-0109")])
    with pytest.raises(ValueError, match="conflicts"):
        _build(env, path)


def test_filled_sheet_rows_can_be_copied_as_they_are(env):
    row = dict(next(r for r in env["sheet"] if r["source_system"] == "shop"),
               decided_account_id="ACC-0098", reviewer="Test Reviewer", decided_on="2026-10-15", reason="test")
    out, _ = _build(env, _write(env["tmp"]("cp") / "d.csv", [row]))
    assert out["decisions_applied"] == int(row["records"])


def test_apply_reports_the_effect(env):
    shop = next(r for r in env["sheet"] if r["source_system"] == "shop")
    path = _write(env["tmp"]("ap") / "d.csv", [_decision("shop", shop["source_key"], shop["candidate_1"])])
    from pipeline.audit.log import verify
    before = verify()["by_action"].get("review_decisions_applied", 0)
    out = apply(env["s"], path)
    assert verify()["by_action"].get("review_decisions_applied", 0) == before + 1      # one audit entry (D-045)
    b, a = out["before"], out["after"]
    moved = sum(n for src, cust, n in a["moved"] if src == "shop" and cust == shop["candidate_1"])
    assert moved > 0 and b["unknown_rows"] - a["unknown_rows"] == moved
    assert round(b["unknown_revenue"]["BL5"] - a["unknown_revenue"].get("BL5", 0.0), 2) == round(float(shop["orders_total"]), 2)
    assert a["at_risk"] and all(len(r) == 4 for r in a["at_risk"])
    assert "truth" not in str(out).lower()
