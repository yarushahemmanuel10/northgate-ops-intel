"""Layer 1 ingestion: structure checks, lineage, exact text, shop via API only, no answer key."""
import csv
import json
import re
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from generator.config import load_config
from generator.messiness import build as build_raw
from pipeline.ingest import schemas
from pipeline.ingest.common import Rejected, SchemaError
from pipeline.ingest.files import read_csv
from pipeline.ingest.run import ingest
from pipeline.ingest.shop_api import ShopApiUnavailable, fetch_orders
from pipeline.settings import load_settings
from shop_api.app import create_app

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def raw_dir(tmp_path_factory):
    raw = tmp_path_factory.mktemp("raw")
    build_raw(load_config(), raw, tmp_path_factory.mktemp("truth"))
    return raw


@pytest.fixture(scope="module")
def settings():
    return load_settings()


def _client(raw_dir):
    return TestClient(create_app(raw_dir / "shop_orders.json"), base_url="http://testserver")


@pytest.fixture(scope="module")
def staged(settings, raw_dir, tmp_path_factory):
    out = tmp_path_factory.mktemp("staging")
    s = dict(settings, shop_api=dict(settings["shop_api"], base_url="http://testserver"))
    counts = ingest(s, client=_client(raw_dir), raw_dir=raw_dir, staging_dir=out)
    return counts, out, s


def _read(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


# --- the pipeline never touches the answer key ---------------------------------------------------

def test_pipeline_never_reads_answer_key_or_generator():
    banned = ["ground_truth", "data/clean", "\"clean\"", "_meta.yaml", "generator.yaml",
              "from generator", "import generator", "shop_orders.json"]
    for path in (ROOT / "pipeline").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for b in banned:
            assert b not in text, f"{path.relative_to(ROOT)} mentions {b}"
    cfg = (ROOT / "config" / "pipeline.yaml").read_text(encoding="utf-8")
    assert "ground_truth" not in cfg and "clean" not in cfg
    scanned = {p.relative_to(ROOT).as_posix() for p in (ROOT / "pipeline").rglob("*.py")}
    assert {"pipeline/warehouse/dedup.py", "pipeline/warehouse/load.py"} <= scanned     # warehouse code is scanned
    for name in ("warehouse.yaml", "review_decisions.csv", "vocabulary.yaml", "resolution.yaml", "validation.yaml"):
        text = (ROOT / "config" / name).read_text(encoding="utf-8")
        assert "ground_truth" not in text and "known_issues" not in text, name


# --- counts and rejected rows -----------------------------------------------------------------------

def test_counts(staged, raw_dir):
    counts, out, _ = staged
    raw_orders = json.loads((raw_dir / "shop_orders.json").read_text(encoding="utf-8"))["orders"]
    assert counts["crm_accounts"] == 124 and counts["crm_contract_lines"] == 225
    assert counts["helpdesk_tickets"] == 1535
    assert counts["telecoms_contracts"] == 59 and counts["telecoms_bills"] == 899
    assert counts["shop_orders"] == len(raw_orders) == 3228
    assert counts["kb_documents"] == 50
    assert counts["rejected_rows"] == 0


def test_quality_problems_pass_through_unchanged(staged):
    _, out, _ = staged
    hd = _read(out / "helpdesk_tickets.csv")
    assert {"P5", "Urgent", "N/A"} & {r["priority"] for r in hd}
    assert any(re.fullmatch(r"\d{2}/\d{2}/\d{4} \d{2}:\d{2}", r["opened_at"]) for r in hd)
    lines = _read(out / "crm_contract_lines.csv")
    assert any(v["Annual Value (GBP)"].startswith("£") or v["Annual Value (GBP)"].endswith("k") for v in lines)
    orders = _read(out / "shop_orders.csv")
    assert {"cancelled", "refunded"} <= {o["status"] for o in orders}


# --- exact text and lineage ------------------------------------------------------------------------------

def test_csv_values_exactly_as_exported(staged, raw_dir):
    _, out, s = staged
    for name, file in (("helpdesk_tickets", "helpdesk_tickets.csv"), ("telecoms_bills", "telecoms_bills.csv"),
                       ("telecoms_contracts", "telecoms_contracts.csv")):
        with open(raw_dir / file, encoding="utf-8", newline="") as f:
            f.readline()
            raw = list(csv.DictReader(f))
        staged_rows = _read(out / f"{name}.csv")
        assert len(raw) == len(staged_rows)
        for r, st in zip(raw, staged_rows):
            assert all(st[k] == v for k, v in r.items())
            assert st["_source_file"] == file and st["_ingested_at"] == s["as_of_moment"]


def test_lineage_row_numbers_point_at_the_file(staged, raw_dir):
    _, out, _ = staged
    lines = (raw_dir / "telecoms_bills.csv").read_text(encoding="utf-8").splitlines()
    for st in _read(out / "telecoms_bills.csv")[:50]:
        assert lines[int(st["_source_row"]) - 1].startswith(st["bill_id"] + ",")


def test_shop_orders_match_the_file_exactly(staged, raw_dir):
    _, out, _ = staged
    raw = json.loads((raw_dir / "shop_orders.json").read_text(encoding="utf-8"), parse_float=str, parse_int=str)
    by_id = {o["order_id"]: o for o in raw["orders"]}
    staged_orders = _read(out / "shop_orders.csv")
    assert sorted(o["order_id"] for o in staged_orders) == sorted(by_id)
    assert [o["order_id"] for o in staged_orders] == sorted(by_id)            # API pages in order-ID order
    for st in staged_orders:
        src = by_id[st["order_id"]]
        for k in schemas.SHOP_ORDER:
            if k != "lines":
                assert st[k] == ("" if src[k] is None else str(src[k])), (st["order_id"], k)
        assert st["_source_row"] == st["order_id"] and int(st["_source_page"]) >= 1
    staged_lines = _read(out / "shop_order_lines.csv")
    assert len(staged_lines) == sum(len(o["lines"]) for o in raw["orders"])


def test_kb_front_matter_and_exact_body(staged):
    _, out, _ = staged
    docs = {d["id"]: d for d in _read(out / "kb_documents.csv")}
    assert len(docs) == 50 and docs["KB-0026"]["status"] == "superseded"
    assert docs["KB-0026"]["superseded_by"] == "KB-0015" and docs["KB-0026"]["superseded_on"] == "2025-11-03"
    for d in docs.values():
        text = (ROOT / "knowledge_base" / d["_source_file"]).read_text(encoding="utf-8")
        assert text.split("---\n", 2)[2] == d["body"]


# --- structure checks ----------------------------------------------------------------------------------------

def _write(path, lines):
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_missing_marker_missing_or_unexpected_column_fail_clearly(tmp_path):
    hdr = ",".join(schemas.TELECOMS_BILLS)
    row = "BILL-1,TEL-1,2025-04,2025-04-30,1,1,2"
    with pytest.raises(SchemaError, match="marker"):
        read_csv(_write(tmp_path / "a.csv", [hdr, row]), schemas.TELECOMS_BILLS, "t", "x", Rejected())
    with pytest.raises(SchemaError, match="Missing: \\['total'\\]"):
        read_csv(_write(tmp_path / "b.csv", ["# SYNTHETIC DATA", hdr.rsplit(",", 1)[0], row]),
                 schemas.TELECOMS_BILLS, "t", "x", Rejected())
    with pytest.raises(SchemaError, match="unexpected: \\['extra'\\]"):
        read_csv(_write(tmp_path / "c.csv", ["# SYNTHETIC DATA", hdr + ",extra", row + ",1"]),
                 schemas.TELECOMS_BILLS, "t", "x", Rejected())


def test_wrong_column_count_is_rejected_not_fatal(tmp_path):
    hdr = ",".join(schemas.TELECOMS_BILLS)
    rej = Rejected()
    rows = read_csv(_write(tmp_path / "d.csv", ["# SYNTHETIC DATA", hdr, "BILL-1,TEL-1,2025-04,2025-04-30,1,1,2",
                                                  "BILL-2,TEL-1,2025-05"]), schemas.TELECOMS_BILLS, "t", "x", rej)
    assert len(rows) == 1 and len(rej.rows) == 1 and rej.rows[0]["source_row"] == "4"


def test_shop_api_unreachable_stops_without_fallback(settings):
    with httpx.Client(timeout=2) as client:
        with pytest.raises(ShopApiUnavailable, match="does not fall back"):
            fetch_orders(client, "http://127.0.0.1:9", 100, settings["as_of_moment"], Rejected())


# --- determinism ----------------------------------------------------------------------------------------------

def test_two_ingests_identical(settings, raw_dir, tmp_path):
    s = dict(settings, shop_api=dict(settings["shop_api"], base_url="http://testserver"))
    ingest(s, client=_client(raw_dir), raw_dir=raw_dir, staging_dir=tmp_path / "a")
    ingest(s, client=_client(raw_dir), raw_dir=raw_dir, staging_dir=tmp_path / "b")
    for f in sorted((tmp_path / "a").iterdir()):
        assert f.read_bytes() == (tmp_path / "b" / f.name).read_bytes(), f.name
