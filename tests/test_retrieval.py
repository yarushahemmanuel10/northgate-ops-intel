"""Knowledge-base search (step 5.2, D-035)."""
import builtins
import filecmp

import duckdb
import pytest
from fastapi.testclient import TestClient

from eval.kb_questions import FILES, load
from eval.retrieval_eval import report
from generator.config import load_config
from generator.messiness import build as build_raw
from pipeline.ingest.run import ingest
from pipeline.retrieval import index as kb
from pipeline.retrieval.bm25 import BM25
from pipeline.retrieval.text import stem, tokens
from pipeline.settings import load_settings
from pipeline.validate.run import load_rules, validate
from shop_api.app import create_app


@pytest.fixture(scope="module")
def idx(tmp_path_factory, monkeypatch_module):
    m = tmp_path_factory.mktemp
    raw, truth, staging, validated, out, out2 = m("raw"), m("truth"), m("staging"), m("validated"), m("idx"), m("idx2")
    build_raw(load_config(), raw, truth)
    s = load_settings()
    s = dict(s, staging_dir=staging, retrieval_dir=out, shop_api=dict(s["shop_api"], base_url="http://testserver"))
    ingest(s, client=TestClient(create_app(raw / "shop_orders.json"), base_url="http://testserver"),
           raw_dir=raw, staging_dir=staging)
    validate(s, load_rules(), staging, validated)
    kb.build(s, validated, out)
    kb.build(s, validated, out2)
    path = out / "kb_index.duckdb"
    # make search() and the evaluation use this index
    monkeypatch_module.setattr(kb, "load_settings", lambda: dict(load_settings(), retrieval_dir=out))
    return {"path": path, "path2": out2 / "kb_index.duckdb", "validated": validated}


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


def _q(path, sql):
    con = duckdb.connect(str(path), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def test_heldout_questions_are_refused_without_opening_the_file(monkeypatch):
    opened = []
    real_open = builtins.open
    monkeypatch.setattr(builtins, "open", lambda f, *a, **k: (opened.append(str(f)), real_open(f, *a, **k))[1])
    with pytest.raises(PermissionError):
        load("heldout")
    assert str(FILES["heldout"]) not in opened
    assert len(load("tune")) == 20


def test_tokens_are_general():
    assert tokens("The printers aren't printing!") == ["printer", "aren", "print"]
    assert stem("policies") == "policy" and stem("access") == "access" and stem("updated") == "updat"


def test_bm25_prefers_rarer_matching_terms():
    bm = BM25([["printer", "offline"], ["printer", "toner"], ["email", "full"]])
    s = bm.scores(["printer", "offline"])
    assert s[0] > s[1] > s[2] == 0.0


def test_sections_carry_their_article_title(idx):
    rows = _q(idx["path"], "SELECT title, heading, indexed_text FROM kb_section")
    assert len(rows) > 200 and all(t.startswith(f"{title}. {heading}. ") for title, heading, t in rows)


def test_superseded_articles_are_listed_not_indexed(idx):
    assert _q(idx["path"], "SELECT COUNT(*) FROM kb_section WHERE kb_id = 'KB-0026'")[0][0] == 0
    assert _q(idx["path"], "SELECT kb_id, superseded_by FROM kb_superseded") == [("KB-0026", "KB-0015")]


def test_near_duplicates_stay_separate_for_the_mfa_new_phone_question(idx):
    t04 = next(q for q in load("tune") if q["id"] == "T04")
    assert "new phone" in t04["question"] and t04["type"] == "conflict"
    top5 = [r["kb_id"] for r in kb.search(t04["question"], k=5)]
    assert {"KB-0008", "KB-0030"} <= set(top5)


def test_tuning_retrieval_floor_and_kb0026_never_returned(idx):
    out = report(load("tune"))
    hits, n = out["hit_rate"]
    assert n == 17 and hits == 17                                 # regression guard: every answerable question hit@3
    assert not out["kb0026_returned"]
    assert all(not r["bad_in_top5"] for r in out["rows"])
    assert all(r["all_expected_in_top5"] for r in out["rows"] if r["type"] == "multi_doc")


def test_index_build_is_deterministic(idx):
    assert _q(idx["path"], "SELECT * FROM kb_section ORDER BY section_id") == \
        _q(idx["path2"], "SELECT * FROM kb_section ORDER BY section_id")
