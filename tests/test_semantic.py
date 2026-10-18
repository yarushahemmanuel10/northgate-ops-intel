"""Semantic layer (step 2.4): data dictionary, metric definitions and the ticket-category
vocabulary. Data is rebuilt in temporary folders from the generator, so the coverage checks
run against exactly what the pipeline writes."""
import csv
import re
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from eval.validation_report import answer_key_scores
from generator.config import load_config
from generator.messiness import build as build_raw
from pipeline.ingest.run import ingest
from pipeline.resolve.run import load_config as load_resolution, resolve
from pipeline.settings import ROOT, load_settings
from pipeline.validate.run import Validator, category_lookup, load_rules, validate
from pipeline.warehouse.dedup import load_config as load_warehouse, run as find_duplicates
from pipeline.warehouse.load import build as build_warehouse
from pipeline.warehouse.review_sheet import build_sheet, SHEET_COLUMNS
from pipeline.ingest.common import write_staging
import duckdb
from shop_api.app import create_app

DICTIONARY = yaml.safe_load((ROOT / "semantic" / "data_dictionary.yaml").read_text(encoding="utf-8"))
METRICS = yaml.safe_load((ROOT / "semantic" / "metrics.yaml").read_text(encoding="utf-8"))["metrics"]
VOCABULARY = yaml.safe_load((ROOT / "config" / "vocabulary.yaml").read_text(encoding="utf-8"))
BQS = re.findall(r"^### (BQ-\d\d)\b", (ROOT / "docs" / "03_requirements.md").read_text(encoding="utf-8"), re.M)
TYPES = {"text", "date", "timestamp", "decimal", "integer", "boolean", "code"}


def _read(p):
    with open(p, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _fields():
    for table, t in DICTIONARY["tables"].items():
        for col, spec in t["columns"].items():
            yield table, col, spec
        for col in t["lineage"]:
            yield table, col, DICTIONARY["lineage_columns"][col]


@pytest.fixture(scope="module")
def dirs(tmp_path_factory):
    m = tmp_path_factory.mktemp
    raw, truth, staging, validated, resolved = m("raw"), m("truth"), m("staging"), m("validated"), m("resolved")
    warehouse = m("warehouse")
    build_raw(load_config(), raw, truth)
    s = load_settings()
    s = dict(s, staging_dir=staging, shop_api=dict(s["shop_api"], base_url="http://testserver"))
    ingest(s, client=TestClient(create_app(raw / "shop_orders.json"), base_url="http://testserver"),
           raw_dir=raw, staging_dir=staging)
    validate(s, load_rules(), staging, validated)
    resolve(s, load_resolution(), validated, resolved)
    find_duplicates(s, load_warehouse(), validated, warehouse)
    build_warehouse(s, load_warehouse(), validated, resolved, warehouse)
    write_staging(build_sheet(validated, resolved, warehouse / "duplicates.csv"), SHEET_COLUMNS, warehouse / "review_sheet.csv")
    from pipeline.alerts.run import run as run_alerts
    alerts_db = m("alerts") / "alerts.duckdb"
    run_alerts(s, warehouse_db=warehouse / "northgate.duckdb", alerts_db=alerts_db)
    from pipeline.retrieval.index import build as build_index
    retrieval = m("retrieval")
    build_index(s, validated, retrieval)
    return {"truth": truth, "validated": validated, "resolved": resolved, "warehouse": warehouse, "alerts_db": alerts_db,
            "retrieval_db": retrieval / "kb_index.duckdb"}


# ---- data dictionary ----------------------------------------------------------------------

def test_every_column_has_an_entry_and_every_entry_a_column(dirs):
    files = {f.stem: f for d in ("validated", "resolved", "warehouse") for f in dirs[d].glob("*.csv")}
    db, layer_of = {}, {}
    for layer, path in (("warehouse", dirs["warehouse"] / "northgate.duckdb"), ("alerts", dirs["alerts_db"]),
                        ("retrieval", dirs["retrieval_db"])):
        con = duckdb.connect(str(path), read_only=True)
        for table, column in con.execute("SELECT table_name, column_name FROM information_schema.columns").fetchall():
            db.setdefault(table, set()).add(column)
            layer_of[table] = layer
        con.close()
    assert not set(files) & set(db)
    assert set(files) | set(db) == set(DICTIONARY["tables"]), "a table is missing from the dictionary, or listed but not written"
    for name in db:
        t = DICTIONARY["tables"][name]
        entries = set(t["columns"]) | set(t["lineage"])
        assert db[name] == entries, f"{name}: missing {sorted(db[name] - entries)}, extra {sorted(entries - db[name])}"
        assert t["layer"] == layer_of[name], name
    for name, f in files.items():
        with open(f, encoding="utf-8", newline="") as fh:
            columns = set(csv.DictReader(fh).fieldnames)
        t = DICTIONARY["tables"][name]
        entries = set(t["columns"]) | set(t["lineage"])
        assert not columns - entries, f"{name}: columns with no dictionary entry: {sorted(columns - entries)}"
        assert not entries - columns, f"{name}: dictionary entries with no column: {sorted(entries - columns)}"
        layer = next(d for d in ("validated", "resolved", "warehouse") if f.parent == dirs[d])
        assert t["layer"] == layer, name


def test_every_field_is_fully_described():
    for table, col, spec in _fields():
        where = f"{table}.{col}"
        assert spec["description"].strip(), where
        assert spec["type"] in TYPES, where
        assert isinstance(spec["personal_data"], bool), f"{where}: personal_data must be true or false"
        assert all(b in BQS for b in spec["bqs"]), where
        assert spec["bqs"] or spec.get("unused_reason"), f"{where}: no BQ and no reason it is unused"


def test_personal_data_flags_for_key_fields():
    cols = {(t, c): s for t, c, s in _fields()}
    assert cols[("helpdesk_tickets", "description")]["personal_data"] is True
    assert cols[("helpdesk_tickets", "agent")]["personal_data"] is True
    assert cols[("crm_accounts", "Account Manager")]["personal_data"] is True
    for t, c in (("helpdesk_tickets", "customer_name"), ("crm_accounts", "Company Name"),
                 ("shop_orders", "company_name"), ("telecoms_contracts", "account_name")):
        assert cols[(t, c)]["personal_data"] is False                     # business names


# ---- metric definitions -------------------------------------------------------------------

def _section(doc: str, number: str) -> str:
    m = re.search(rf"^### {re.escape(number)} .*?(?=^### |^## |\Z)", doc, re.M | re.S)
    assert m, f"section {number} not found"
    return m.group(0)


def _config_value(file: str, key: str):
    v = yaml.safe_load((ROOT / file).read_text(encoding="utf-8"))
    for part in key.split("."):
        v = v[part]
    return v


def test_every_metric_number_comes_from_the_fact_sheet_or_config():
    for name, mt in METRICS.items():
        assert mt["definition"].strip() and mt["name"].strip(), name
        for n in mt["numbers"]:
            if n["source"].startswith("docs/"):
                section = _section((ROOT / n["source"]).read_text(encoding="utf-8"), n["section"])
                rows = [ln for ln in section.splitlines() if n["label"] in ln and str(n["value"]) in ln]
                assert rows, f"{name}: {n['label']!r} / {n['value']!r} not in {n['source']} §{n['section']}"
            else:
                assert n["source"].startswith("config/"), name
                assert _config_value(n["source"], n["key"]) == n["value"], f"{name}: {n['key']}"


def test_metric_definitions_quote_their_numbers():
    # a number listed for a metric must also appear in the definition text the assistant quotes
    for name, mt in METRICS.items():
        for n in mt["numbers"]:
            v = str(n["value"])
            if name in ("as_of_moment", "open_ticket"):
                continue                                      # quoted in words, not as written in the source
            assert v in mt["definition"] or v.replace("–", " to ") in mt["definition"], f"{name}: {v}"


def test_metric_inputs_exist_in_the_dictionary():
    known = {f"{t}.{c}" for t, c, _ in _fields()}
    for name, mt in METRICS.items():
        assert all(b in BQS for b in mt["bqs"]) and mt["bqs"], name
        for i in mt["inputs"]:
            assert i in known, f"{name}: input {i} is not a dictionary field"


def test_every_business_question_is_used():
    assert BQS == [f"BQ-{i:02d}" for i in range(1, 17)]
    used = {b for _, _, s in _fields() for b in s["bqs"]} | {b for mt in METRICS.values() for b in mt["bqs"]}
    assert not set(BQS) - used, f"business questions with no field or metric: {sorted(set(BQS) - used)}"


# ---- ticket-category vocabulary -----------------------------------------------------------

def test_vocabulary_variants_were_seen_in_the_data(dirs):
    seen = {t["category"].strip().casefold() for t in _read(dirs["validated"] / "helpdesk_tickets.csv")}
    voc = VOCABULARY["ticket_category"]
    for value in list(voc["canonical"]) + list(voc["variants"]):
        assert value.casefold() in seen, f"{value!r} is in the vocabulary but never appears in data/validated/"
    assert set(voc["judgement"]) <= set(voc["variants"])


def test_unmapped_category_keeps_its_text_and_is_flagged():
    v = Validator(load_rules())
    row = {"ticket_id": "INC-99999", "_source_row": "1", "opened_at": "", "first_responded_at": "", "closed_at": "",
           "priority": "P3", "category": "Telephony"}
    v.tickets([row])
    assert row["category"] == "Telephony" and row["category__value"] == ""
    assert [f["rule_id"] for f in v.findings if f["field"] == "category"] == ["CATEGORY-001"]


def test_category_lookup_ignores_case_and_rejects_bad_targets():
    lookup = category_lookup(VOCABULARY)
    assert lookup["hardware"] == "Hardware" and lookup["h/w"] == "Hardware"
    bad = {"ticket_category": {"canonical": ["Email"], "variants": {"Mail": "E-mail"}, "judgement": {}}}
    with pytest.raises(ValueError):
        category_lookup(bad)


def test_qi06_category_scores(dirs):
    # optimistic: the variants in the data are the ones that were injected (D-022)
    s = answer_key_scores(dirs["validated"], dirs["truth"])["QI-06 ticket categories"]
    assert s["known"] > 0
    assert s["mapping_recall"] >= 0.95 and s["mapping_precision"] >= 0.95
    ticket_categories = {t["category__value"] for t in _read(dirs["validated"] / "helpdesk_tickets.csv")}
    assert ticket_categories <= set(VOCABULARY["ticket_category"]["canonical"]) | {""}
