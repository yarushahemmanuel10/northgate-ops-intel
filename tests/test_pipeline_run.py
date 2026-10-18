"""Whole-chain run and Power BI exports (step 3.5, D-027). The shop API is served by FastAPI's
test client, so no server runs."""
import csv
import io
import re

import duckdb
import httpx
import pytest
from fastapi.testclient import TestClient

from generator.config import load_config
from generator.messiness import build as build_raw
from pipeline import run as pipeline_run
from pipeline.ingest.shop_api import ShopApiUnavailable
from pipeline.settings import load_settings
from shop_api.app import create_app

DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _settings(raw, root):
    s = load_settings()
    s = dict(s, raw_dir=raw, staging_dir=root / "staging", validated_dir=root / "validated",
             resolved_dir=root / "resolved", warehouse_dir=root / "warehouse", alerts_dir=root / "alerts", retrieval_dir=root / "retrieval")
    return dict(s, shop_api=dict(s["shop_api"], base_url="http://testserver"))


def _client(raw):
    return TestClient(create_app(raw / "shop_orders.json"), base_url="http://testserver")


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    raw, truth = tmp_path_factory.mktemp("raw"), tmp_path_factory.mktemp("truth")
    build_raw(load_config(), raw, truth)
    out = []
    for name in ("run1", "run2"):
        s = _settings(raw, tmp_path_factory.mktemp(name))
        out.append((s, pipeline_run.run_all(s, client=_client(raw))))
    return {"raw": raw, "runs": out}


def _contents(db):
    con = duckdb.connect(str(db), read_only=True)
    try:
        names = [r[0] for r in con.execute("SELECT table_name FROM information_schema.tables ORDER BY 1").fetchall()]
        return {n: con.execute(f"SELECT * FROM {n} ORDER BY ALL").fetchall() for n in names}
    finally:
        con.close()


def test_rebuilding_twice_gives_identical_results(runs):
    (s1, o1), (s2, o2) = runs["runs"]
    a, b = _contents(s1["warehouse_dir"] / "northgate.duckdb"), _contents(s2["warehouse_dir"] / "northgate.duckdb")
    assert set(a) == set(b) and len(a) >= 13
    for name in a:
        assert a[name] == b[name], name
    for f in sorted((s1["warehouse_dir"] / "exports").iterdir()):
        assert f.read_bytes() == (s2["warehouse_dir"] / "exports" / f.name).read_bytes(), f.name
    assert o1["summary"] == o2["summary"]


def test_summary_figures(runs):
    summary = runs["runs"][0][1]["summary"]
    revenue = {bl: (basis, round(total, 2)) for bl, basis, total in summary["revenue"]}
    assert revenue["BL2"] == ("measured", 1072853.46) and revenue["BL5"] == ("measured", 734394.00)
    assert revenue["BL1"][0] == revenue["BL3"][0] == revenue["BL4"][0] == "modelled"
    # built with the committed review decisions, which can only move revenue off the Unknown customer;
    # the no-decisions figure (34,271.50) is pinned in tests/test_revenue.py, built with an empty file
    db = runs["runs"][0][0]["warehouse_dir"] / "northgate.duckdb"
    con = duckdb.connect(str(db), read_only=True)
    on_unknown = con.execute("SELECT COALESCE(SUM(revenue), 0) FROM v_revenue WHERE customer_id = 'UNKNOWN'").fetchone()[0]
    con.close()
    assert round(summary["unknown_revenue"], 2) == round(on_unknown, 2) <= 34271.50 and summary["unknown_rows"] > 0


def test_exports_are_clean_for_power_bi(runs):
    s, out = runs["runs"][0]
    folder = s["warehouse_dir"] / "exports"
    files = sorted(folder.glob("*.csv"))
    assert {f.stem for f in files} == set(out["exported"])
    assert "ticket_text" not in out["exported"] and not (folder / "ticket_text.csv").exists()
    assert "SYNTHETIC DATA" in (folder / "README.txt").read_text(encoding="utf-8")
    con = duckdb.connect(str(s["warehouse_dir"] / "northgate.duckdb"), read_only=True)
    types = dict(con.execute("SELECT table_name || '.' || column_name, data_type FROM information_schema.columns").fetchall())
    for f in files:
        text = f.read_bytes().decode("utf-8")                                   # valid UTF-8
        assert not text.startswith("#") and not text.startswith("﻿"), f.name
        rows = list(csv.DictReader(io.StringIO(text)))
        assert len(rows) == out["exported"][f.stem], f.name
        for col in rows[0].keys() if rows else []:
            t = types[f"{f.stem}.{col}"]
            values = [r[col] for r in rows if r[col] != ""]
            if t == "DATE":
                assert all(DATE.match(v) for v in values), f"{f.name}.{col}"
            elif t.startswith(("DECIMAL", "DOUBLE", "INTEGER", "BIGINT")):
                assert all("," not in v for v in values), f"{f.name}.{col}"      # no thousands separators
                [float(v) for v in values]                                       # '.' decimal mark parses
    # full precision: the CSV adds up to the database total
    csv_total = sum(float(r["revenue"]) for r in csv.DictReader(open(folder / "v_revenue.csv", encoding="utf-8")))
    db_total = con.execute("SELECT SUM(revenue) FROM v_revenue").fetchone()[0]
    con.close()
    assert abs(csv_total - db_total) < 1e-6
    assert any(len(r["revenue"].split(".")[-1]) > 2 for r in csv.DictReader(open(folder / "v_revenue.csv", encoding="utf-8")))


def test_stops_cleanly_when_the_shop_api_is_down(runs, tmp_path):
    s = _settings(runs["raw"], tmp_path)
    s["shop_api"]["base_url"] = "http://127.0.0.1:9"                        # nothing listens here
    with pytest.raises(ShopApiUnavailable):
        pipeline_run.run_all(s, client=httpx.Client(timeout=1))
    assert not (tmp_path / "staging").exists() and not (tmp_path / "warehouse").exists()


def test_main_prints_the_ingestion_message_and_exits_2(runs, tmp_path, monkeypatch, capsys):
    s = _settings(runs["raw"], tmp_path)
    s["shop_api"] = dict(s["shop_api"], base_url="http://127.0.0.1:9", timeout_seconds=1)
    monkeypatch.setattr(pipeline_run, "load_settings", lambda: s)
    with pytest.raises(SystemExit) as exc:
        pipeline_run.main()
    assert exc.value.code == 2
    assert "Ingestion stopped: Cannot reach the shop API" in capsys.readouterr().err
