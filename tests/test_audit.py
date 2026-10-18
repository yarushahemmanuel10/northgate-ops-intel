"""Audit log (step 6.4, D-045): hash chain, anchor, retention, no personal data, one entry per action."""
import datetime as dt
import json
import os

import pytest
from streamlit.testing.v1 import AppTest

from pipeline.audit import log as audit
from pipeline.roles.assistant import RoleAssistant
from pipeline.settings import ROOT
from tests.test_assistant import FakeClient, env, text, tool_use  # noqa: F401  (env is a fixture)
from tests.test_dashboard import db  # noqa: F401  (fixture)
from tests.test_masking import built  # noqa: F401  (fixture)


def entries(d):
    p = d / audit.LOG_NAME
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def write_three(d):
    for i in range(3):
        audit.record("page_opened", role="data_steward", session="s1", target=f"p{i}", directory=d,
                     warehouse_db=d / "none.duckdb")


# ---- chain, anchor, retention ------------------------------------------------------------------------

def test_chain_head_and_anchor(tmp_path):
    write_three(tmp_path)
    r = audit.verify(tmp_path)
    es = entries(tmp_path)
    assert r["ok"] and r["entries"] == 3 and r["head"] == es[-1]["hash"] and es[0]["prev"] == audit.GENESIS
    assert [e["seq"] for e in es] == [1, 2, 3] and es[1]["prev"] == es[0]["hash"]
    assert audit.write_head(tmp_path) == es[-1]["hash"]
    assert audit.verify(tmp_path)["anchor"]["seq"] == 3


@pytest.mark.parametrize("tamper,expect", [
    ("edit", "contents changed"),
    ("delete", "does not follow"),
    ("reorder", "does not follow"),
])
def test_tampering_is_detected(tmp_path, tamper, expect):
    write_three(tmp_path)
    p = tmp_path / audit.LOG_NAME
    lines = p.read_text().splitlines()
    if tamper == "edit":
        e = json.loads(lines[1]); e["role"] = "operations_director"; lines[1] = json.dumps(e)
    elif tamper == "delete":
        del lines[1]
    else:
        lines[1], lines[2] = lines[2], lines[1]
    p.write_text("\n".join(lines) + "\n")
    r = audit.verify(tmp_path)
    assert not r["ok"] and any(expect in x for x in r["problems"]), r["problems"]


def test_cutting_entries_off_the_end_is_caught_by_the_anchor(tmp_path):
    write_three(tmp_path)
    audit.write_head(tmp_path)
    p = tmp_path / audit.LOG_NAME
    p.write_text("\n".join(p.read_text().splitlines()[:2]) + "\n")
    r = audit.verify(tmp_path)
    assert not r["ok"] and any("head.txt" in x for x in r["problems"])


def test_known_limit_a_full_consistent_rewrite_is_not_detected(tmp_path):
    """D-045: someone with write access to both files can rebuild the whole log; production needs write-once storage."""
    write_three(tmp_path)
    audit.write_head(tmp_path)
    (tmp_path / audit.LOG_NAME).unlink()
    audit.record("page_opened", role="x", session="forged", directory=tmp_path, warehouse_db=tmp_path / "none.duckdb")
    audit.write_head(tmp_path)
    assert audit.verify(tmp_path)["ok"]


def test_retention_is_reported_not_enforced(tmp_path):
    write_three(tmp_path)
    later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=400)
    r = audit.verify(tmp_path, now=later)
    assert r["retention_days"] == 365 and r["older_than_retention"] == 3 and r["entries"] == 3
    assert len(entries(tmp_path)) == 3                                         # nothing deleted


def test_verify_command_prints_head_and_retention(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("NORTHGATE_AUDIT_DIR", str(tmp_path))
    write_three(tmp_path)
    with pytest.raises(SystemExit) as e:
        audit.main()
    out = capsys.readouterr().out
    assert e.value.code == 0 and f"Head hash: {entries(tmp_path)[-1]['hash']}" in out and "365-day retention" in out


# ---- no personal data --------------------------------------------------------------------------------

def test_no_personal_data_or_business_names_in_any_entry(tmp_path, built):  # noqa: F811
    import duckdb
    con = duckdb.connect(str(built["db"]), read_only=True)
    try:
        company = con.execute("SELECT company_name FROM dim_customer WHERE NOT is_unknown LIMIT 1").fetchone()[0]
        staff = con.execute("SELECT account_manager FROM dim_customer WHERE account_manager IS NOT NULL LIMIT 1").fetchone()[0]
    finally:
        con.close()
    audit.record("assistant_question", role="account_manager", session="s", directory=tmp_path, warehouse_db=built["db"],
                 target=f"note about {company}",
                 detail={"question": f"Did {staff} call {company}? Contact Sam Lee on 07123 456789 or sam.lee@firm.co.uk",
                         "list": [company, "fine"]})
    blob = (tmp_path / audit.LOG_NAME).read_text().lower()
    for bad in (company.lower(), staff.lower(), "sam lee", "07123 456789", "sam.lee@firm.co.uk"):
        assert bad not in blob, bad
    assert "[customer]" in blob and "[phone]" in blob and "[email]" in blob


# ---- one entry per action ----------------------------------------------------------------------------

@pytest.fixture
def audit_env(db, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setenv("NORTHGATE_DB", str(db))
    monkeypatch.setenv("NORTHGATE_ALERTS_DB", str(db.parent / "alerts.duckdb"))
    monkeypatch.setenv("NORTHGATE_AUDIT_DIR", str(tmp_path))
    monkeypatch.delenv("NORTHGATE_ROLE", raising=False)
    return tmp_path


def test_dashboard_session_page_role_change_one_entry_each(audit_env):
    at = AppTest.from_file(str(ROOT / "dashboard" / "app.py"), default_timeout=60)
    at.session_state["role"] = "account_manager"
    at.run()
    assert [e["action"] for e in entries(audit_env)] == ["session_started", "page_opened"]
    at.run()                                                                      # a rerun is not a new action
    assert len(entries(audit_env)) == 2
    at.sidebar.selectbox[0].set_value("data_steward").run()
    es = entries(audit_env)
    assert [e["action"] for e in es[2:]] == ["role_changed", "page_opened"]
    assert es[2]["role"] == "data_steward" and es[2]["detail"]["from"] == "account_manager"
    assert len({e["session"] for e in es}) == 1 and len(es[0]["session"]) == 16
    assert audit.verify(audit_env)["ok"]


def test_refused_page_is_logged(audit_env, monkeypatch):
    monkeypatch.setenv("NORTHGATE_ROLE", "data_steward")
    AppTest.from_file(str(ROOT / "dashboard" / "pages" / "overview.py"), default_timeout=60).run()
    es = [e for e in entries(audit_env) if e["action"].startswith("page_")]
    assert [(e["action"], e["target"], e["outcome"], e["role"]) for e in es] == \
        [("page_refused", "overview", "refused", "data_steward")]


def test_assistant_question_and_refused_tool_one_entry_each(env, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setenv("NORTHGATE_AUDIT_DIR", str(tmp_path / "a"))
    a = RoleAssistant("data_steward", client=FakeClient([tool_use(("renewals", {})), text("The system can't answer that.")]),
                      warehouse_db=env["w"], alerts_db=env["a"], index_path=env["idx"], out_dir=tmp_path)
    a.ask("How many renewals?")
    es = entries(tmp_path / "a")
    assert [e["action"] for e in es] == ["tool_refused", "assistant_question"]
    assert es[0]["target"] == "renewals" and es[1]["outcome"] == "declined" and es[1]["detail"]["tools"] == ["renewals"]
    assert es[0]["session"] == es[1]["session"] == a.session
