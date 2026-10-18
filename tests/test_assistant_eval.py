"""Evaluation harness (step 5.5, D-038): scoring rules and the held-out guard. No API calls."""
import builtins
import subprocess
import sys

import pytest

from eval import assistant_eval as ev
from eval.kb_questions import DATA_FILES, FILES, load, load_data
from pipeline.settings import ROOT


def out(answer, declined=False, cited_kb=(), cited_views=(), returned=(), tools=(), warnings=()):
    return {"answer": answer, "declined": declined, "cited_kb": list(cited_kb), "cited_views": list(cited_views),
            "sources_returned": list(returned), "tools": [{"name": t} for t in tools], "warnings": list(warnings)}


KBQ = {"id": "X1", "type": "fact", "expected_sources": ["KB-0001", "KB-0002"], "must_not_cite": ["KB-0026"],
       "answer_must_include": ["printer restart", "toner"]}


def test_kb_scoring():
    good = ev.score_kb(KBQ, out("Restart the printer and check the toner [KB-0002].", cited_kb=["KB-0002"],
                                returned=["KB-0002", "KB-0009"]))
    assert ev.passed(good) and good["must_include"][0] == "pass"
    bad = ev.score_kb(KBQ, out("See KB-0026.", cited_kb=["KB-0026"], returned=["KB-0026"]))
    assert bad["retrieval_hit"][0] == bad["citation"][0] == "fail" and "must_not_cite" in bad["citation"][1]
    declined = ev.score_kb(KBQ, out("The knowledge base doesn't answer this.", declined=True, returned=["KB-0001"]))
    assert declined["abstention"] == ("fail", "declined an answerable question")
    abstain = dict(KBQ, type="abstain", expected_sources=[], answer_must_include=[])
    assert ev.passed(ev.score_kb(abstain, out("The knowledge base doesn't answer this.", declined=True)))
    assert ev.score_kb(abstain, out("Do X [KB-0001].", cited_kb=["KB-0001"]))["abstention"][0] == "fail"


def test_conflict_recommendation_is_rough_and_reported():
    q = dict(KBQ, type="conflict", answer_must_include=[])
    a = "KB-0001 says A; KB-0002 says B. They conflict; the service desk lead should resolve it."
    assert ev.score_kb(q, out(a, cited_kb=["KB-0001", "KB-0002"], returned=["KB-0001"]))["no_recommendation"][0] == "pass"
    r = ev.score_kb(q, out(a + " The safer course is A.", cited_kb=["KB-0001"], returned=["KB-0001"]))
    assert r["no_recommendation"] == ("fail", "recommendation words: ['safer']") and ev.passed(r)   # rough: not counted


def test_data_scoring_reads_the_expected_value_at_run_time(monkeypatch):
    truth = {"sources": ["v_kpi_bq05_renewals"], "totals": {"lines": 36, "value": 459400.0}}
    monkeypatch.setattr(ev.data_tools, "call", lambda *a, **k: truth)
    q = {"id": "D1", "type": "data", "expected_tool": "renewals", "expected_args": {},
         "checks": [{"path": "totals.lines", "kind": "count", "tolerance": 0},
                    {"path": "totals.value", "kind": "money", "tolerance": 1.0}]}
    ok = ev.score_data(q, out("36 lines, annual contract value £459,400 [v_kpi_bq05_renewals].",
                              cited_views=["v_kpi_bq05_renewals"], tools=["renewals"]))
    assert ev.passed(ok)
    wrong = ev.score_data(q, out("37 lines, £459,402 [v_revenue].", cited_views=["v_revenue"], tools=["revenue_monthly"]))
    assert {k for k, v in wrong.items() if v[0] == "fail"} == {"tool_used", "figures", "citation"}
    assert "totals.lines=36" in wrong["figures"][1] and "totals.value=459400" in wrong["figures"][1]
    abstain = {"id": "D2", "type": "data_abstain", "expected_tool": None, "checks": []}
    assert ev.passed(ev.score_data(abstain, out("The system can't answer that.", declined=True)))
    assert not ev.passed(ev.score_data(abstain, out("It is 91%.", warnings=["Figures not found in any tool result: 91%."])))


def test_heldout_option_is_refused_without_approval_and_opens_nothing(monkeypatch, tmp_path):
    assert not ev.APPROVAL.exists()
    r = subprocess.run([sys.executable, "-m", "eval.assistant_eval", "--heldout", "--out", str(tmp_path)],
                       cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 3 and "Refused" in r.stderr and not list(tmp_path.iterdir())
    opened = []
    real = builtins.open
    monkeypatch.setattr(builtins, "open", lambda f, *a, **k: (opened.append(str(f)), real(f, *a, **k))[1])
    for loader in (load, load_data):
        with pytest.raises(PermissionError):
            loader("heldout")
    assert str(FILES["heldout"]) not in opened and str(DATA_FILES["heldout"]) not in opened
    assert len(load("tune")) == 20 and len(load_data("tune")) == 8
