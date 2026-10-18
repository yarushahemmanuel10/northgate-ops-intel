"""Masking personal data in ticket text (step 6.1, D-043). Integration checks use the dev half only."""
import csv
import hashlib

import duckdb
import pytest
from fastapi.testclient import TestClient

from generator.config import load_config
from generator.messiness import build as build_raw
from pipeline.ingest.run import ingest
from pipeline.mask.text import Lists, mask
from pipeline.resolve.run import load_config as load_resolution, resolve
from pipeline.settings import ROOT, load_settings
from pipeline.validate.run import load_rules, validate
from pipeline.warehouse import export
from pipeline.warehouse.dedup import load_config as load_warehouse, run as find_duplicates
from pipeline.warehouse.load import build as build_warehouse
from shop_api.app import create_app


def m(text, **lists):
    return mask(text, Lists(**lists))[0]


@pytest.mark.parametrize("text,expected", [
    ("Contact Marie Ali on 01632 960235.", "Contact [NAME] on [PHONE]."),
    ("Spoke to Jennifer McDonald.", "Spoke to [NAME]."),
    ("Ring Sean O'Brien on 020 7946 0018 or +44 (0)161 496 0000", "Ring [NAME] on [PHONE] or [PHONE]"),
    ("Mobile 07123-456-789, office (0113) 496 0000", "Mobile [PHONE], office [PHONE]"),
    ("Please email a.person@firm.co.uk when fixed", "Please email [EMAIL] when fixed"),
    ("Mrs Patel called", "Mrs [NAME] called"),
    ("Contact Sam Singh Monday", "Contact [NAME] Monday"),
    ("Contact June Cross today", "Contact [NAME] today"),
])
def test_general_rules_mask(text, expected):
    assert m(text) == expected


@pytest.mark.parametrize("text", [
    "Ticket INC-00340 raised 01/02/2026 for £1,200.50",
    "Order 12345678901234 is late",
    "Contact Outlook Support about Windows",
    "Spoke to the user about Teams",
    "Restart and call back",
])
def test_ordinary_text_is_left_alone(text):
    assert m(text) == text


def test_name_in_an_email_address_is_masked_elsewhere_and_lists_are_used():
    assert m("Hugh Matthews says: write to hugh.matthews@x.org") == "[NAME] says: write to [EMAIL]"
    assert m("Assigned to Kim Lowe for Acme Widgets Ltd", staff={"Kim Lowe"}, customers={"Acme Widgets Ltd"}) == \
        "Assigned to [NAME] for [CUSTOMER]"


def test_masking_uses_general_rules_not_the_generator():
    code = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "pipeline" / "mask").glob("*.py"))
    for banned in ("faker", "from generator", "import generator", ".example", "01632", "07700 900", "ground_truth"):
        assert banned not in code, banned


# ---- integration ------------------------------------------------------------------------------------

def dev(ticket_id):
    return int(hashlib.md5(ticket_id.encode()).hexdigest(), 16) % 2 == 0


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    mk = tmp_path_factory.mktemp
    raw, truth, staging, validated, resolved, warehouse = (mk("raw"), mk("truth"), mk("staging"), mk("validated"),
                                                          mk("resolved"), mk("warehouse"))
    build_raw(load_config(), raw, truth)
    s = load_settings()
    s = dict(s, staging_dir=staging, warehouse_dir=warehouse, shop_api=dict(s["shop_api"], base_url="http://testserver"))
    ingest(s, client=TestClient(create_app(raw / "shop_orders.json"), base_url="http://testserver"),
           raw_dir=raw, staging_dir=staging)
    validate(s, load_rules(), staging, validated)
    resolve(s, load_resolution(), validated, resolved)
    find_duplicates(s, load_warehouse(), validated, warehouse)
    build_warehouse(s, load_warehouse(), validated, resolved, warehouse)
    export.run(s, load_warehouse())
    with open(truth / "known_issues.csv", encoding="utf-8", newline="") as fh:
        next(fh)
        spans = [(r["record_key"], r["new_value"]) for r in csv.DictReader(fh)
                 if r["qi"] == "QI-07" and r["op"] == "pii_span" and dev(r["record_key"])]
    return {"db": warehouse / "northgate.duckdb", "exports": warehouse / "exports", "dev_spans": spans}


def test_masked_table_has_every_ticket_and_no_dev_personal_data(built):
    con = duckdb.connect(str(built["db"]), read_only=True)
    try:
        n_text, n_masked = (con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("ticket_text", "ticket_text_masked"))
        masked = dict(con.execute("SELECT ticket_id, description_masked FROM ticket_text_masked").fetchall())
        view_cols = {r[0] for r in con.execute("SELECT column_name FROM information_schema.columns "
                                               "WHERE table_name = 'v_ticket_masked'").fetchall()}
    finally:
        con.close()
    assert n_text == n_masked > 1000
    assert len(built["dev_spans"]) > 200
    left = [(t, v) for t, v in built["dev_spans"] if v in (masked[t] or "")]
    assert not left, f"{len(left)} dev spans left unmasked"
    assert "problem_text_masked" in view_cols and not any("description" in c for c in view_cols)


def test_exports_carry_the_masked_text_only(built):
    files = {p.stem for p in built["exports"].glob("*.csv")}
    assert "ticket_text_masked" in files and "ticket_text" not in files
    blob = "\n".join(p.read_text(encoding="utf-8") for p in built["exports"].glob("*.csv"))
    assert not [v for _, v in built["dev_spans"] if v in blob]


def test_dashboard_shows_masked_tickets_from_the_view():
    service = (ROOT / "dashboard" / "pages" / "service.py").read_text(encoding="utf-8")
    assert "FROM v_ticket_masked" in service and "ticket_text" not in service
