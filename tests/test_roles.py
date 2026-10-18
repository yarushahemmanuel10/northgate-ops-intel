"""Demo roles (step 6.3, D-044): every role against every page and every assistant tool."""
import json
import os

import pytest
from streamlit.testing.v1 import AppTest

from pipeline.assistant import kb_tool
from pipeline.assistant.ask import Assistant
from pipeline.roles.assistant import RoleAssistant
from pipeline.roles.config import ALERT_RULES, PAGES, get_role, load_roles
from pipeline.settings import ROOT
from pipeline.tools import data as data_tools
from tests.test_assistant import FakeClient, env, text, tool_use  # noqa: F401  (env is a fixture)
from tests.test_dashboard import db  # noqa: F401  (fixture)

ROLES, DEFAULT = load_roles()
ALL_TOOLS = [kb_tool.TOOL["name"]] + [t["name"] for t in data_tools.TOOLS]
DASH = ROOT / "dashboard"


def test_roles_config_as_agreed():
    assert set(ROLES) == {"service_desk_agent", "account_manager", "operations_director", "data_steward"}
    assert DEFAULT == "service_desk_agent"
    od = ROLES["operations_director"]
    assert list(od.tools) == ALL_TOOLS and set(od.pages) == set(PAGES) and set(od.alert_rules) == set(ALERT_RULES)
    ds = ROLES["data_steward"]
    assert ds.pages == ("data_quality",) and ds.shows_names("data_quality")
    assert not any(ds.can_open(p) for p in ("overview", "customers"))           # no revenue or customer pages
    assert ROLES["service_desk_agent"].names_on == ()
    with pytest.raises(ValueError):
        get_role("admin")


# ---- dashboard: every role x every page --------------------------------------------------------------

@pytest.fixture(scope="module")
def dash_env(db):  # noqa: F811
    keep = {k: os.environ.get(k) for k in ("NORTHGATE_DB", "NORTHGATE_ALERTS_DB", "NORTHGATE_ROLE")}
    os.environ["NORTHGATE_DB"] = str(db)
    os.environ["NORTHGATE_ALERTS_DB"] = str(db.parent / "alerts.duckdb")
    yield db
    for k, v in keep.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _columns(at):
    return [c for df in at.dataframe for c in df.value.columns]


@pytest.mark.parametrize("role", sorted(ROLES))
@pytest.mark.parametrize("page", PAGES)
def test_every_role_against_every_page(dash_env, role, page):
    os.environ["NORTHGATE_ROLE"] = role
    at = AppTest.from_file(str(DASH / "pages" / f"{page}.py"), default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    r = ROLES[role]
    refused = any("cannot open this page" in e.value for e in at.error)
    assert refused == (not r.can_open(page))
    if refused:
        assert not at.dataframe and not at.metric                               # nothing shown after the refusal
        return
    cols = _columns(at)
    name_cols = {"Name", "Name as written"} & set(cols)
    assert bool(name_cols) == r.shows_names(page), (role, page, cols)
    if page == "alerts":
        rules = {v for df in at.dataframe for v in (df.value["Rule"] if "Rule" in df.value else [])}
        assert rules <= set(r.alert_rules), (role, rules)


@pytest.mark.parametrize("role", sorted(ROLES))
def test_navigation_lists_only_the_roles_pages(dash_env, role):
    os.environ.pop("NORTHGATE_ROLE", None)
    at = AppTest.from_file(str(DASH / "app.py"), default_timeout=60)
    at.session_state["role"] = role
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    sel = at.sidebar.selectbox[0]
    assert sel.label == "Demo role — not authentication" and sel.value == role
    assert any(ROLES[role].label in c.value for c in at.caption)                # landed on one of its own pages


# ---- assistant: every role x every tool --------------------------------------------------------------

def role_assistant(env, tmp_path, role, replies):  # noqa: F811
    client = FakeClient(replies)
    return RoleAssistant(role, client=client, warehouse_db=env["w"], alerts_db=env["a"], index_path=env["idx"],
                         out_dir=tmp_path), client


def frozen_assistant(env, tmp_path, replies):  # noqa: F811
    client = FakeClient(replies)
    return Assistant(client=client, warehouse_db=env["w"], alerts_db=env["a"], index_path=env["idx"],
                     out_dir=tmp_path), client


ARGS = {"search_knowledge_base": {"query": "printer offline"}, "metric_definition": {"name": "open_ticket"}}


@pytest.mark.parametrize("role", sorted(ROLES))
def test_every_role_against_every_tool(env, tmp_path, role):  # noqa: F811
    r = ROLES[role]
    calls = [(t, ARGS.get(t, {})) for t in ALL_TOOLS]
    a, client = role_assistant(env, tmp_path, role, [tool_use(*calls), text("Done.")])
    out = a.ask("Run every tool.")
    offered = [t["name"] for t in client.requests[0]["tools"]]
    assert offered == list(r.tools)                                              # the model is only offered the role's tools
    results = {b["tool_use_id"]: json.loads(b["content"]) for b in client.requests[1]["messages"][-1]["content"]}
    for i, (name, _) in enumerate(calls):
        res = results[f"t{i}"]
        refused = res.get("error", {}).get("code") == "not_allowed"
        assert refused == (name not in r.tools), (role, name, res.get("error"))
    assert out["role"] == role


def test_operations_director_sends_exactly_the_frozen_requests(env, tmp_path):  # noqa: F811
    script = [tool_use(("search_knowledge_base", {"query": "new phone authenticator"}), ("renewals", {})),
              tool_use(("at_risk_customers", {})), text("Answer [v_at_risk].")]
    frozen, fc = frozen_assistant(env, tmp_path / "f", list(script))
    od, oc = role_assistant(env, tmp_path / "o", "operations_director", list(script))
    a, b = frozen.ask("Same question"), od.ask("Same question")
    assert len(fc.requests) == len(oc.requests) == 3
    assert json.dumps(fc.requests, sort_keys=True) == json.dumps(oc.requests, sort_keys=True)
    assert a["answer"] == b["answer"] and a["display"] == b["display"]


def test_roles_without_names_get_ids_only(env, tmp_path):  # noqa: F811
    cid = "ACC-0027"
    for role, named in (("service_desk_agent", False), ("account_manager", True)):
        a, _ = role_assistant(env, tmp_path / role, role, [text(f"{cid} needs attention.")])
        out = a.ask("Who needs attention?")
        assert (out["display"] != out["answer"]) == named and cid in out["display"]
