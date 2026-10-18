"""The assistant (step 5.4, D-037), tested with a fake client: no API call is made here."""
import json

import pytest
from fastapi.testclient import TestClient

from generator.config import load_config
from generator.messiness import build as build_raw
from pipeline.alerts.run import run as run_alerts
from pipeline.assistant import kb_tool
from pipeline.assistant.ask import Assistant, check_answer
from pipeline.assistant.guard import Forbidden, LeakBlocked, check_outgoing
from pipeline.assistant.llm import MissingKey, cost_usd, load_config as assistant_config, read_api_key
from pipeline.ingest.run import ingest
from pipeline.resolve.run import load_config as load_resolution, resolve
from pipeline.retrieval.index import build as build_index
from pipeline.settings import load_settings
from pipeline.tools import data as data_tools
from pipeline.validate.run import load_rules, validate
from pipeline.warehouse.dedup import load_config as load_warehouse, run as find_duplicates
from pipeline.warehouse.load import build as build_warehouse
from shop_api.app import create_app


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    m = tmp_path_factory.mktemp
    raw, truth, staging, validated, resolved, warehouse, alerts, idx = (
        m("raw"), m("truth"), m("staging"), m("validated"), m("resolved"), m("warehouse"), m("alerts"), m("idx"))
    build_raw(load_config(), raw, truth)
    s = load_settings()
    s = dict(s, staging_dir=staging, shop_api=dict(s["shop_api"], base_url="http://testserver"))
    ingest(s, client=TestClient(create_app(raw / "shop_orders.json"), base_url="http://testserver"),
           raw_dir=raw, staging_dir=staging)
    validate(s, load_rules(), staging, validated)
    resolve(s, load_resolution(), validated, resolved)
    find_duplicates(s, load_warehouse(), validated, warehouse)
    build_warehouse(s, load_warehouse(), validated, resolved, warehouse)
    w, a = warehouse / "northgate.duckdb", alerts / "alerts.duckdb"
    run_alerts(s, warehouse_db=w, alerts_db=a)
    build_index(s, validated, idx)
    return {"w": w, "a": a, "idx": idx / "kb_index.duckdb", "forbidden": Forbidden.load(w)}


class FakeClient:
    """Plays back scripted replies and records every request it is sent."""
    def __init__(self, replies):
        self.replies, self.requests = list(replies), []
        self.messages = self

    def create(self, **request):
        self.requests.append(json.loads(json.dumps(request)))
        reply = self.replies.pop(0)

        class R:
            def model_dump(self, mode=None):
                return reply
        return R()


def tool_use(*calls):
    return {"content": [{"type": "tool_use", "id": f"t{i}", "name": n, "input": a} for i, (n, a) in enumerate(calls)],
            "stop_reason": "tool_use", "usage": {"input_tokens": 3000, "output_tokens": 50}}


def text(t):
    return {"content": [{"type": "text", "text": t}], "stop_reason": "end_turn",
            "usage": {"input_tokens": 4000, "output_tokens": 120}}


def assistant(env, tmp_path, replies):
    client = FakeClient(replies)
    return Assistant(client=client, warehouse_db=env["w"], alerts_db=env["a"], index_path=env["idx"],
                     out_dir=tmp_path), client


# ---- key ------------------------------------------------------------------------------------------

def test_key_comes_from_env_file_only(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-environment")
    with pytest.raises(MissingKey) as e:
        read_api_key(tmp_path / ".env")
    assert "from-environment" not in str(e.value)
    f = tmp_path / ".env"
    f.write_text("# comment\nOTHER=1\nANTHROPIC_API_KEY='sk-test-123'\n")
    assert read_api_key(f) == "sk-test-123"
    f.write_text("ANTHROPIC_API_KEY=\n")
    with pytest.raises(MissingKey):
        read_api_key(f)


def test_key_file_is_git_ignored():
    import subprocess
    from pipeline.settings import ROOT
    assert subprocess.run(["git", "check-ignore", "-q", ".env"], cwd=ROOT).returncode == 0


# ---- what leaves the machine ----------------------------------------------------------------------

def every_tool_call():
    calls = [("search_knowledge_base", {"query": q}) for q in
             ("printer offline", "new phone authenticator app", "password reset", "VPN", "account manager customer")]
    for t in data_tools.TOOLS:
        props = t["input_schema"]["properties"]
        base = {"limit": data_tools.ROW_CAP} if "limit" in props else {}
        if t["name"] == "metric_definition":
            calls += [(t["name"], {"name": n}) for n in props["name"]["enum"]]
            continue
        calls.append((t["name"], dict(base)))
        for key, p in props.items():
            for v in p.get("enum", [True, False] if p["type"] == "boolean" else []):
                calls.append((t["name"], dict(base, **{key: v})))
    return calls


def test_no_name_or_ticket_text_in_any_outgoing_request(env, tmp_path):
    f = env["forbidden"]
    name, cid = next((n, c) for n, c in sorted(f.names.items()) if c)
    calls = every_tool_call()
    chunks = [calls[i:i + 10] for i in range(0, len(calls), 10)]
    cfg = dict(assistant_config(), max_tool_rounds=len(chunks) + 1)
    a = Assistant(client=FakeClient([tool_use(*c) for c in chunks] + [text("Done.")]), warehouse_db=env["w"],
                  alerts_db=env["a"], index_path=env["idx"], out_dir=tmp_path, config=cfg)
    out = a.ask(f"How is {name.title()} doing on tickets and revenue?")
    assert not out["stopped"], out["answer"]
    sent = [json.dumps(r, ensure_ascii=False).lower() for r in a.caller.client.requests]
    assert len(sent) == len(chunks) + 1
    blob = "\n".join(sent)
    assert cid.lower() in blob                       # the typed name went out as its customer ID
    for n in f.names:
        assert n not in blob
    for s in f.staff:
        assert s not in blob
    for t in f.ticket_text:
        assert t not in blob
    assert 'kb_id' in blob and 'heading' in blob and "v_kpi_bq05_renewals" in blob   # KB text and tool results did go


def test_guard_refuses_names_staff_and_ticket_text(env):
    f = env["forbidden"]
    for value in (next(iter(f.names)), next(iter(f.staff)), next(iter(f.ticket_text))):
        with pytest.raises(LeakBlocked) as e:
            check_outgoing({"messages": [{"role": "user", "content": f"x {value.upper()} y"}]}, f)
        assert value not in str(e.value).lower()
    check_outgoing({"messages": [{"role": "user", "content": "ACC-0027 and the Unknown customer"}]}, f)


def test_a_blocked_request_is_never_sent(env, tmp_path, monkeypatch):
    staff = next(iter(env["forbidden"].staff))
    monkeypatch.setattr(kb_tool, "search", lambda q, cfg, p=None: {"sources": [], "results": [{"text": staff}]})
    a, client = assistant(env, tmp_path, [tool_use(("search_knowledge_base", {"query": "anything"}))])
    out = a.ask("Who looks after the printers?")
    assert out["stopped"] and "Nothing left the machine" in out["answer"] and len(client.requests) == 1


# ---- names, cost, cache and budget ------------------------------------------------------------------

def test_names_are_added_after_the_reply(env, tmp_path):
    cid, name = next((c, n) for n, c in sorted(env["forbidden"].names.items()) if c)
    a, client = assistant(env, tmp_path, [tool_use(("at_risk_customers", {})),
                                          text(f"{cid} is the top retention priority [v_at_risk].")])
    out = a.ask("Which customer is most at risk?")
    assert name not in out["answer"].lower() and name in out["display"].lower() and f"({cid})" in out["display"]
    assert all(name not in json.dumps(r).lower() for r in client.requests)


def test_cost_log_cache_and_budget(env, tmp_path):
    prices = assistant_config()["prices"]
    assert cost_usd({"input_tokens": 10_000, "output_tokens": 600}, prices) == pytest.approx(0.0013)
    replies = [tool_use(("as_of", {})), text("The data runs to 2026-09-30 [v_meta].")]
    a, client = assistant(env, tmp_path, replies)
    first = a.ask("What is the as-of date?")
    assert first["api_calls"] == 2 and first["cost_gbp"] == pytest.approx(cost_usd({"input_tokens": 7000, "output_tokens": 170}, prices))
    log = [json.loads(x) for x in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert set(log[0]) == {"time", "model", "cached", "input_tokens", "output_tokens", "cache_creation_input_tokens",
                           "cache_read_input_tokens", "cost_usd", "cost_gbp"} and log[0]["model"] == "claude-haiku-5-5"
    again = a.ask("What is the as-of date?")                      # identical requests: served from the cache
    assert again["api_calls"] == 0 and again["cost_gbp"] == 0 and again["answer"] == first["answer"]
    assert len(client.requests) == 2
    with open(tmp_path / "calls.jsonl", "a") as fh:
        fh.write(json.dumps({"cost_gbp": 5.0}) + "\n")
    stopped = a.ask("A new question that is not cached")
    assert stopped["stopped"] and "Spending limit reached" in stopped["answer"] and len(client.requests) == 2


# ---- honest answers ---------------------------------------------------------------------------------

def test_conflicting_articles_come_together(env):
    cfg = assistant_config()["knowledge_base"]
    out = kb_tool.search("re-register authenticator app on a new phone", cfg, env["idx"])
    assert {"KB-0008", "KB-0030"} <= set(out["sources"]) and out["warnings"]
    assert "KB-0026" not in json.dumps(out)


def test_weak_evidence_and_bad_query(env):
    cfg = assistant_config()["knowledge_base"]
    assert kb_tool.search("zzqx wobble frobnicate", cfg, env["idx"])["weak_evidence"] is True
    assert kb_tool.search("", cfg, env["idx"])["error"]["code"] == "bad_parameter"


def test_tool_errors_go_back_to_the_model(env, tmp_path):
    a, client = assistant(env, tmp_path, [tool_use(("sla_compliance", {"priority": "P9"})),
                                          text("The system can't answer that.")])
    out = a.ask("SLA for P9?")
    sent = json.dumps(client.requests[1]["messages"][-1])
    assert "bad_parameter" in sent and out["declined"] and not out["stopped"]


def test_answer_checks():
    cfg = assistant_config()["knowledge_base"]
    kb = {"sources": ["KB-0008", "KB-0030"], "results": []}
    data = {"sources": ["v_kpi_bq05_renewals"], "totals": {"lines": 36, "annual_value": 459400.0}}
    ok = check_answer("36 lines renew, worth £459,400 [v_kpi_bq05_renewals].", "next 90 days?", [data], cfg)
    assert ok["warnings"] == []
    bad = check_answer("37 lines renew [v_kpi_bq05_renewals] [v_revenue]; see [KB-0026].", "q", [data], cfg)
    assert any("KB-0026" in w for w in bad["warnings"]) and bad["unverified_figures"] == ["37"]
    assert any("v_revenue" in w for w in bad["warnings"])
    one_side = check_answer("Agents must do it [KB-0008].", "q", [kb], cfg)
    assert any("conflicting" in w for w in one_side["warnings"])
    both = check_answer("KB-0008 says agents must; KB-0030 says users can. They conflict.", "q", [kb], cfg)
    assert both["warnings"] == []
    assert check_answer("About £0.46m a year [v_kpi_bq05_renewals].", "q", [data], cfg)["warnings"] == []


def test_env_example_holds_no_key():
    """.env.example is committed, so it may name the variable but never carry a value."""
    from pipeline.settings import ROOT
    lines = (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    keys = [l for l in lines if l.strip().startswith("ANTHROPIC_API_KEY")]
    assert keys == ["ANTHROPIC_API_KEY="], "ANTHROPIC_API_KEY= in .env.example must have nothing after it"
    assert all(l.strip().startswith("#") or l.strip().endswith("=") or not l.strip() for l in lines)


def test_api_failure_ends_the_question_with_a_message(env, tmp_path):
    import anthropic
    import httpx

    class Refusing(FakeClient):
        def create(self, **request):
            r = httpx.Response(401, text="Unauthorized", request=httpx.Request("POST", "https://api.anthropic.com"))
            raise anthropic.AuthenticationError("Unauthorized", response=r, body=None)
    a = Assistant(client=Refusing([]), warehouse_db=env["w"], alerts_db=env["a"], index_path=env["idx"], out_dir=tmp_path)
    out = a.ask("What is the as-of date?")
    assert out["stopped"] and "HTTP 401 from a proxy" in out["answer"] and not (tmp_path / "calls.jsonl").exists()


def test_only_bracketed_citations_count_and_brackets_split():
    from pipeline.assistant.ask import citations
    cfg = assistant_config()["knowledge_base"]
    kb = {"sources": ["KB-0008", "KB-0030", "KB-0015"], "results": []}
    assert citations("KB-0026 was replaced by KB-0015 [KB-0015].") == ({"KB-0015"}, set())
    assert check_answer("The older KB-0026 was replaced [KB-0015].", "q", [kb], cfg)["warnings"] == []
    assert citations("x [v_at_risk, v_kpi_bq06_summary] y [semantic/metrics.yaml; KB-0001 step 2][KB-0002]") == \
        ({"KB-0001", "KB-0002"}, {"v_at_risk", "v_kpi_bq06_summary", "semantic/metrics.yaml"})
    assert check_answer("The search also returned KB-0008 and", "q", [kb], cfg)["warnings"] == []   # named, not cited
    assert any("conflicting" in w for w in check_answer("Agents do it [KB-0030].", "q", [kb], cfg)["warnings"])


def test_numbered_list_markers_are_not_figures():
    from pipeline.assistant.ask import _numbers
    assert [w for w, _, _ in _numbers("Steps:\n1. Reset it\n2) Wait 12 hours\n  3. Done, 4 users")] == ["12", "4"]
