"""Ask the Northgate assistant a question (step 5.4, D-037).

Run:  python -m pipeline.assistant.ask "How many contract lines renew in the next 90 days?"
      python -m pipeline.assistant.ask --spend        # total logged spend

The model gets two kinds of tool: search_knowledge_base (D-035) and the 13 read-only data tools (D-036). It
chooses which to call; the tools run here, on this machine, and only their results go back. After the reply,
code (not the model) checks citations and figures against the tool results, then adds business names to
customer IDs for display.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from pipeline.assistant import kb_tool
from pipeline.assistant.guard import Forbidden, LeakBlocked, names_to_ids
from pipeline.assistant.llm import ApiFailed, BudgetExceeded, Caller, MissingKey, load_config
from pipeline.tools import data as data_tools
from pipeline.tools.names import add_names, lookup

SYSTEM = """You are the operations assistant for Northgate IT Services Ltd. The data is synthetic; it runs to an as-of date of 30 September 2026.

Rules:
1. Answer only from tool results. Use search_knowledge_base for how-to, procedure and policy questions; use the data tools for figures about tickets, SLAs, customers, revenue, renewals, alerts and data quality. Call as_of if you need the date or data window.
2. Every figure you give must appear in a tool result. Put the source in square brackets straight after it: a knowledge-base ID such as [KB-0012], or a view name from the result's "sources", such as [v_kpi_bq05_renewals]. Cite a knowledge-base article only if its text supports what you say. Show every amount of money with the £ sign and say what it is, for example "annual contract value £459,400" or "revenue £1,685,375.00".
3. Use the totals in a result; never add up rows yourself. If a result says it was truncated, say only part of the list is shown.
4. Refer to customers by their ID only (for example ACC-0027). Never write a business name or a person's name; the system adds names later.
5. If the knowledge-base sections returned do not clearly answer the question (weak_evidence is true, or the sections are about something else), say "The knowledge base doesn't answer this." and do not guess. You may name the closest articles.
6. If two articles disagree, or a result carries a conflict warning, give both positions with their citations, say plainly that they conflict, and say that the service desk lead, who keeps the knowledge base up to date, should resolve it. Do not recommend either side or suggest what to do in the meantime.
7. If no tool provides what is asked (for example a breakdown the tool does not offer, or one customer's ticket history), say "The system can't answer that.", give the reason, and say what the system can answer instead. Do not add figures that were not asked for, and never present a different figure as the answer. The same applies when you decline a knowledge-base question: give the reason, without extra figures.
8. Be brief: a few sentences or a short list."""

DECLINE_KB = "The knowledge base doesn't answer this."
DECLINE_DATA = "The system can't answer that."
NUMBER = re.compile(r"(?<![A-Za-z0-9\-#.])£?(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s*(%|k\b|m\b|million\b|bn\b)?")
DATES = re.compile(r"\b\d{4}-\d{2}(-\d{2})?\b|\b\d{1,2}(st|nd|rd|th)?\s+(January|February|March|April|May|June|July|"
                   r"August|September|October|November|December)(\s+\d{4})?\b|\b(19|20)\d{2}\b", re.IGNORECASE)


class Assistant:
    def __init__(self, client=None, warehouse_db: Path | None = None, alerts_db: Path | None = None,
                 index_path: Path | None = None, out_dir: Path | None = None, config: dict | None = None):
        self.cfg = config or load_config()
        dw, da = data_tools.default_paths()
        self.warehouse_db, self.alerts_db = warehouse_db or dw, alerts_db or da
        self.index_path = index_path
        self.forbidden = Forbidden.load(self.warehouse_db)
        self.caller = Caller(self.forbidden, client=client, config=self.cfg, out_dir=out_dir)
        self.tools = [kb_tool.TOOL] + data_tools.TOOLS

    def _run_tool(self, name: str, args: dict) -> dict:
        if name == kb_tool.TOOL["name"]:
            if set(args) - {"query"}:
                return {"error": {"code": "bad_parameter", "message": "Only 'query' is allowed."}}
            return kb_tool.search(args.get("query"), self.cfg["knowledge_base"], self.index_path)
        return data_tools.call(name, args, warehouse_db=self.warehouse_db, alerts_db=self.alerts_db)

    def ask(self, question: str) -> dict:
        sent_question = names_to_ids(question, self.forbidden)
        messages = [{"role": "user", "content": sent_question}]
        calls, tool_log, results = [], [], []
        try:
            for _ in range(self.cfg["max_tool_rounds"] + 1):
                request = {"model": self.cfg["model"], "max_tokens": self.cfg["max_tokens"], "system": SYSTEM,
                           "tools": self.tools, "messages": messages}
                resp, entry = self.caller.send(request)
                calls.append(entry)
                content = resp["content"]
                if resp.get("stop_reason") != "tool_use":
                    break
                outputs = []
                for block in content:
                    if block["type"] == "tool_use":
                        out = self._run_tool(block["name"], block.get("input") or {})
                        tool_log.append({"name": block["name"], "input": block.get("input") or {}})
                        results.append(out)
                        outputs.append({"type": "tool_result", "tool_use_id": block["id"],
                                        "content": json.dumps(out, ensure_ascii=False)})
                messages += [{"role": "assistant", "content": _clean(content)}, {"role": "user", "content": outputs}]
            else:
                content = [{"type": "text", "text": "Stopped: too many tool rounds for one question."}]
        except (BudgetExceeded, MissingKey, LeakBlocked, ApiFailed) as exc:
            return self._finish(question, sent_question, str(exc), tool_log, results, calls, stopped=True)
        text = "\n".join(b["text"] for b in content if b["type"] == "text").strip()
        return self._finish(question, sent_question, text, tool_log, results, calls)

    def _finish(self, question, sent_question, text, tool_log, results, calls, stopped=False) -> dict:
        checks = {} if stopped else check_answer(text, sent_question, results, self.cfg["knowledge_base"])
        names = {t["name"] for t in tool_log}
        route = ("kb+data" if len(names) > 1 and kb_tool.TOOL["name"] in names else
                 "kb" if names == {kb_tool.TOOL["name"]} else "data" if names else "none")
        return {"question": question, "sent_question": sent_question, "answer": text,
                "display": add_names(text, lookup(self.warehouse_db)), "route": route, "tools": tool_log,
                "declined": DECLINE_KB in text or DECLINE_DATA in text, "stopped": stopped,
                "sources_returned": sorted({x for r in results for x in r.get("sources", [])}),
                "calls": len(calls), "api_calls": sum(not c["cached"] for c in calls),
                "cached_calls": sum(c["cached"] for c in calls),
                "tokens_in": sum(c["input_tokens"] for c in calls if not c["cached"]),
                "tokens_out": sum(c["output_tokens"] for c in calls if not c["cached"]),
                "cost_gbp": round(sum(c["cost_gbp"] for c in calls), 6), **checks}


def _clean(content: list[dict]) -> list[dict]:
    """Send back only the fields the API accepts for earlier assistant turns."""
    out = []
    for b in content:
        if b["type"] == "text":
            out.append({"type": "text", "text": b["text"]})
        elif b["type"] == "tool_use":
            out.append({"type": "tool_use", "id": b["id"], "name": b["name"], "input": b.get("input") or {}})
    return out


LIST_MARKER = re.compile(r"(?m)^\s*\d+[.)]\s")
BRACKET = re.compile(r"\[([^\]]+)\]")


def citations(answer: str) -> tuple[set[str], set[str]]:
    """(KB IDs, other sources) cited in square brackets. Only bracketed citations count (D-039): naming an
    article in a sentence, e.g. "KB-0026 was replaced", is not citing it. One bracket may hold several
    sources separated by commas or semicolons: [v_at_risk, v_kpi_bq06_summary]."""
    kb, other = set(), set()
    for group in BRACKET.findall(answer):
        for item in re.split(r"[,;]", group):
            item = item.strip()
            if not item:
                continue
            m = re.match(r"KB-\d{4}\b", item)
            (kb if m else other).add(m.group(0) if m else item)
    return kb, other


def _numbers(text: str) -> list[tuple[str, float, float]]:
    """(as written, value, tolerance) for each number in the text, ignoring dates, years, IDs and the
    markers of a numbered list ("3. Remind the user...")."""
    out = []
    for m in NUMBER.finditer(DATES.sub(" ", LIST_MARKER.sub(" ", text))):
        whole, frac, unit = m.group(1).replace(",", ""), m.group(2) or "", (m.group(3) or "").lower()
        value, tol = float(whole + frac), 0.5 * 10 ** -(len(frac) - 1 if frac else 0)
        scale = {"k": 1e3, "m": 1e6, "million": 1e6, "bn": 1e9}.get(unit, 1)
        if unit == "%":
            value, tol = value / 100, tol / 100
        out.append((m.group(0).strip(), value * scale, tol * scale))
    return out


def check_answer(answer: str, question: str, results: list[dict], kb_cfg: dict) -> dict:
    """Code checks on the reply: citations must come from this question's tool results, KB-0026 is never
    cited, conflicts are shown both ways, and each figure must match a number in a tool result or the question."""
    warnings = []
    retrieved = {s for r in results for s in r.get("sources", []) if s.startswith("KB-")}
    views = {s for r in results for s in r.get("sources", []) if not s.startswith("KB-")}
    cited_kb, cited_views = citations(answer)
    if "KB-0026" in cited_kb:
        warnings.append("Cites KB-0026, which is superseded by KB-0015.")
    if cited_kb - retrieved:
        warnings.append(f"Cites articles not returned for this question: {', '.join(sorted(cited_kb - retrieved))}.")
    if cited_views - views:
        warnings.append(f"Cites sources not returned for this question: {', '.join(sorted(cited_views - views))}.")
    for pair in kb_cfg.get("conflicts", []):         # fires only when the answer cites one of the pair, not both
        if set(pair) <= retrieved and len(cited_kb & set(pair)) == 1:
            warnings.append(f"Only one of the conflicting articles {' and '.join(pair)} is cited.")
    evidence = [v for _, v, _ in _numbers(question)]
    for r in results:
        evidence += [v for _, v, _ in _numbers(json.dumps(r, ensure_ascii=False))]
    unverified = [w for w, v, tol in _numbers(answer)
                  if not any(abs(v - e) <= tol + 1e-9 or abs(v - e * 100) <= tol + 1e-9 for e in evidence)]
    if unverified:
        warnings.append(f"Figures not found in any tool result: {', '.join(unverified)}.")
    return {"cited_kb": sorted(cited_kb), "cited_views": sorted(cited_views), "unverified_figures": unverified,
            "warnings": warnings}


def main() -> None:
    args = sys.argv[1:]
    if args == ["--spend"]:
        a = Caller.__new__(Caller)
        from pipeline.settings import load_settings
        a.log = Path(load_settings()["assistant_dir"]) / "calls.jsonl"
        print(f"Logged spend: £{Caller.spent_gbp(a):.4f} of £{load_config()['budget']['limit_gbp']:.2f}")
        return
    if not args:
        print('Usage: python -m pipeline.assistant.ask "your question"', file=sys.stderr)
        sys.exit(2)
    out = Assistant().ask(" ".join(args))
    print(out["display"])
    print(f"\n[route: {out['route']}; tools: {', '.join(t['name'] for t in out['tools']) or 'none'}; "
          f"API calls: {out['api_calls']} of {out['calls']}; cost £{out['cost_gbp']:.4f}]")
    for w in out.get("warnings", []):
        print(f"[check: {w}]")


if __name__ == "__main__":
    main()
