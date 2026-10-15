"""Layer 1 validation: rows preserved, documented rules only, scored against the answer key."""
import csv
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from eval.validation_report import answer_key_scores, unseen_scores
from generator.config import load_config
from generator.messiness import build as build_raw
from pipeline.ingest.run import ingest
from pipeline.settings import load_settings
from pipeline.validate.run import load_rules, validate
from shop_api.app import create_app


def _read(p):
    with open(p, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module")
def dirs(tmp_path_factory):
    raw, truth = tmp_path_factory.mktemp("raw"), tmp_path_factory.mktemp("truth")
    staging, validated = tmp_path_factory.mktemp("staging"), tmp_path_factory.mktemp("validated")
    build_raw(load_config(), raw, truth)
    s = load_settings()
    s = dict(s, staging_dir=staging, shop_api=dict(s["shop_api"], base_url="http://testserver"))
    ingest(s, client=TestClient(create_app(raw / "shop_orders.json"), base_url="http://testserver"),
           raw_dir=raw, staging_dir=staging)
    counts = validate(s, load_rules(), staging, validated)
    return {"truth": truth, "staging": staging, "validated": validated, "counts": counts, "settings": s}


@pytest.fixture(scope="module")
def scores(dirs):
    return answer_key_scores(dirs["validated"], dirs["truth"])


def test_every_staging_row_kept(dirs):
    for f in sorted(dirs["staging"].glob("*.csv")):
        if f.name == "rejected_rows.csv":
            continue
        staged, validated = _read(f), _read(dirs["validated"] / f.name)
        assert len(staged) == len(validated), f.name
        for a, b in zip(staged, validated):
            assert all(b[k] == v for k, v in a.items())               # original text untouched


def test_dates_detected_and_converted_exactly(scores):
    s = scores["QI-03 dates"]
    assert s["recall"] == 1.0 and s["converted_exactly_right"] == s["tp"]
    # the only "false positives" are credit notes copied from a bill whose month was already
    # non-ISO: real non-ISO values that the answer key lists only on the original bill
    assert all(table == "telecoms_bills" and key.startswith("CN-") for table, key, _ in s["fp_keys"])


def test_convention_conversions_are_listed_and_ambiguous(dirs):
    rows = [f for f in _read(dirs["validated"] / "validation_findings.csv") if f["rule_id"] == "DATE-002"]
    assert rows
    for f in rows:
        day, month = int(f["original"][:2]), int(f["original"][3:5])
        assert day <= 12 and month <= 12 and f["action"] == "converted_by_convention"


def test_contract_values(scores):
    s = scores["QI-05 contract values"]
    assert s["recall"] == 1.0 and s["fp"] == 0 and s["converted_exactly_right"] == s["tp"]


def test_invalid_priorities_flagged_not_guessed(scores):
    s = scores["QI-05 invalid priority"]
    assert s["tp"] == 5 and s["fp"] == 0 and s["typed_value_empty"]


def test_time_quantity_and_arithmetic(scores):
    for name in ("QI-05 closed before opened", "QI-05 quantity (QTY-001)", "QI-05 quantity (ARITH-001 order total)"):
        assert scores[name]["recall"] == 1.0 and scores[name]["fp"] == 0, name


def test_missing_values_only_where_due(dirs, scores):
    assert scores["QI-08 renewal dates"]["recall"] == 1.0 and scores["QI-08 delivered dates"]["recall"] == 1.0
    assert scores["response SLA measurable"] == 1437
    findings = _read(dirs["validated"] / "validation_findings.csv")
    tickets = {t["ticket_id"]: t for t in _read(dirs["validated"] / "helpdesk_tickets.csv")}
    orders = {o["order_id"]: o for o in _read(dirs["validated"] / "shop_orders.csv")}
    for f in findings:
        if f["rule_id"] == "MISSING-001":
            assert tickets[f["record_key"]]["closed_at"]                  # never an open ticket
        if f["rule_id"] == "MISSING-002":
            assert orders[f["record_key"]]["status"] == "delivered"       # never an undelivered order
    assert not any(f["field"] == "closed_at" and f["rule_id"].startswith("MISSING") for f in findings)


def test_revenue_treatment_is_a_label_not_an_error(dirs, scores):
    s = scores["QI-10 revenue treatment label"]
    assert s["recall"] == 1.0 and s["fp"] == 0
    rules = {f["rule_id"] for f in _read(dirs["validated"] / "validation_findings.csv")}
    assert not any(r.startswith("REVENUE") for r in rules)


def test_unseen_values_never_wrongly_converted():
    u = unseen_scores()
    assert u["summary"].get("WRONGLY converted", 0) == 0


def test_validation_is_deterministic(dirs, tmp_path):
    validate(dirs["settings"], load_rules(), dirs["staging"], tmp_path)
    for f in sorted(dirs["validated"].glob("*.csv")):
        assert f.read_bytes() == (tmp_path / f.name).read_bytes(), f.name
