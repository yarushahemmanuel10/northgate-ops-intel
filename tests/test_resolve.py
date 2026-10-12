"""Entity resolution (step 2.3): full coverage, named cases, held-out precision, the CRM
duplicate rule and determinism. The resolver reads data/validated/ only; this test may read
the answer key because it is evaluation, not pipeline code."""
import csv
import filecmp

import pytest
from fastapi.testclient import TestClient

from eval.resolution_eval import half, load_crosswalk, metrics, named_cases
from generator.config import load_config
from generator.messiness import build as build_raw
from pipeline.ingest.run import ingest
from pipeline.resolve.run import Resolver, load_config as load_resolution, resolve
from pipeline.settings import load_settings
from pipeline.validate.run import load_rules, validate
from shop_api.app import create_app

RESOLVED_TABLES = ["crm_accounts", "helpdesk_tickets", "shop_orders", "telecoms_contracts", "telecoms_bills"]


def _read(p):
    with open(p, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module")
def dirs(tmp_path_factory):
    m = tmp_path_factory.mktemp
    raw, truth, staging, validated = m("raw"), m("truth"), m("staging"), m("validated")
    resolved, resolved2 = m("resolved"), m("resolved2")
    build_raw(load_config(), raw, truth)
    s = load_settings()
    s = dict(s, shop_api=dict(s["shop_api"], base_url="http://testserver"))
    ingest(s, client=TestClient(create_app(raw / "shop_orders.json"), base_url="http://testserver"),
           raw_dir=raw, staging_dir=staging)
    validate(s, load_rules(), staging, validated)
    summary = resolve(s, load_resolution(), validated, resolved)
    resolve(s, load_resolution(), validated, resolved2)
    rows = load_crosswalk(resolved, validated, truth)
    return {"validated": validated, "resolved": resolved, "resolved2": resolved2, "summary": summary, "rows": rows}


def test_every_record_has_exactly_one_crosswalk_row(dirs):
    cw = _read(dirs["resolved"] / "crosswalk.csv")
    expected = sum(len(_read(dirs["validated"] / f"{t}.csv")) for t in RESOLVED_TABLES)
    assert len(cw) == expected
    keys = [(r["source_table"], r["source_row"]) for r in cw]
    assert len(keys) == len(set(keys))


def test_statuses_methods_and_ids_are_valid(dirs):
    crm_ids = {a["Account ID"] for a in _read(dirs["validated"] / "crm_accounts.csv")}
    for r in _read(dirs["resolved"] / "crosswalk.csv"):
        assert r["status"] in {"auto", "review", "unmatched"}
        assert r["method"] in {"exact", "fuzzy", "evidence", "unmatched"}
        if r["status"] == "auto":
            assert r["resolved_account_id"] in crm_ids
        else:
            assert r["resolved_account_id"] == ""            # queued or unmatched records are never guessed
        if r["status"] == "unmatched":
            assert r["method"] == "unmatched"


def test_review_queue_matches_crosswalk(dirs):
    queue = _read(dirs["resolved"] / "review_queue.csv")
    queued_records = sum(int(q["records"]) for q in queue if q["source_system"] != "crm")
    assert queued_records == sum(r["status"] == "review" for r in _read(dirs["resolved"] / "crosswalk.csv"))


def test_named_cases_pass(dirs):
    for case in named_cases(dirs["rows"]):
        assert case["pass"], (case["case"], case["detail"])


def test_held_out_precision_floor(dirs):
    m = metrics(dirs["rows"], "held-out")
    assert m["records"] > 1000 and m["names"] > 100
    assert m["record_precision"] >= 0.99 and m["name_precision"] >= 0.99
    assert m["record_recall"] >= 0.90


def test_no_wrong_auto_matches_on_held_out(dirs):
    wrong = [r for r in dirs["rows"]
             if half(r["truth"]) == "held-out" and r["status"] == "auto" and r["resolved_account_id"] != r["truth"]]
    assert wrong == []


def test_resolution_is_deterministic(dirs):
    for name in ("crosswalk.csv", "review_queue.csv"):
        assert filecmp.cmp(dirs["resolved"] / name, dirs["resolved2"] / name, shallow=False)


# ---- the CRM duplicate rule, on small hand-made records -------------------------------------

def _acc(i, name, since):
    return {"Account ID": i, "Company Name": name, "Customer Since__value": since}


def _line(i):
    return {"Account ID": i, "Business Line": "BL2", "Contract Start__value": "2024-01-01"}


def test_main_record_prefers_contract_lines_over_age():
    r = Resolver(load_resolution(), [_acc("ACC-0001", "Brook Dental Ltd", "2019-01-01"),
                                     _acc("ACC-0002", "Brook Dental Ltd", "2022-01-01")], [_line("ACC-0002")])
    assert r.main_of == {"ACC-0001": "ACC-0002", "ACC-0002": "ACC-0002"}


def test_main_record_falls_back_to_earliest_then_lowest_id():
    r = Resolver(load_resolution(), [_acc("ACC-0001", "Brook Dental", "2022-01-01"),
                                     _acc("ACC-0002", "Brook Dental Ltd", "2019-01-01"),
                                     _acc("ACC-0003", "Kestrel Lane Bakery", "2020-01-01"),
                                     _acc("ACC-0004", "Kestrel Lane Bakery", "2020-01-01")], [])
    assert r.main_of["ACC-0001"] == "ACC-0002"
    assert r.main_of["ACC-0004"] == "ACC-0003"


def test_both_with_contract_lines_are_kept_apart():
    r = Resolver(load_resolution(), [_acc("ACC-0001", "Brook Dental", "2019-01-01"),
                                     _acc("ACC-0002", "Brook Dental", "2022-01-01")],
                 [_line("ACC-0001"), _line("ACC-0002")])
    assert r.main_of == {"ACC-0001": "ACC-0001", "ACC-0002": "ACC-0002"}
    assert r.crm_findings["ACC-0001"][0] == "distinct"


def test_different_legal_forms_go_to_a_person():
    r = Resolver(load_resolution(), [_acc("ACC-0001", "Brook Partners LLP", "2019-01-01"),
                                     _acc("ACC-0002", "Brook Partners Ltd", "2022-01-01")], [])
    assert r.main_of == {"ACC-0001": "ACC-0001", "ACC-0002": "ACC-0002"}
    assert r.crm_findings["ACC-0001"][0] == "review"


def test_missing_threshold_is_refused():
    cfg = load_resolution()
    cfg["decision"]["auto_threshold"] = None
    with pytest.raises(ValueError):
        Resolver(cfg, [], [])
