"""Evaluate the assistant on the reviewer's questions (step 5.5, D-038).

Run:  python -m eval.assistant_eval                  # tuning questions: 20 KB + 8 data (real API calls)
      python -m eval.assistant_eval --cache-only     # same, but only from the response cache (no API calls)
      python -m eval.assistant_eval --heldout        # refused unless eval/heldout_approval.yaml exists

Writes eval/results/assistant_<split>.csv (one row per question, every check with pass/fail and reason)
and eval/results/assistant_<split>_summary.md (pass rates by question type, cost from uncached calls).

Checks (the fields come from eval/questions/README.md; nothing is tuned to individual questions):
  KB questions
    retrieval_hit       answerable: an expected source was among the articles the search returned
    citation            answerable: cites an expected source; any type: cites nothing in must_not_cite
    abstention          abstain: uses a decline sentence; other types: does not
    must_include        ROUGH: each answer_must_include phrase's keywords all appear in the answer
    no_recommendation   ROUGH, conflict questions: no recommendation words (RECOMMENDATION_WORDS)
    post_reply_checks   the assistant's own code checks raised no warning
  Data questions
    tool_used           data/definition: the expected tool was called
    figures             each checks.path value, read from the expected tool now, is in the answer within tolerance
    citation            data/definition: cites a source of the expected tool's result
    abstention          data_abstain: uses a decline sentence; other types: does not
    must_include        ROUGH, as above
    post_reply_checks   as above
A question passes when every check that applies to it passes, rough checks excluded (they are reported apart).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

import yaml

from eval.kb_questions import ROOT, load, load_data
from pipeline.assistant.ask import Assistant, _numbers
from pipeline.assistant.llm import ApiFailed
from pipeline.retrieval.text import tokens
from pipeline.tools import data as data_tools

RESULTS = ROOT / "eval" / "results"
APPROVAL = ROOT / "eval" / "heldout_approval.yaml"
RUN_LIMIT_GBP = 0.50
ROUGH = {"must_include", "no_recommendation"}
# Fixed before the first tuning run (D-038). Lower-case substrings; rough by design.
RECOMMENDATION_WORDS = ["safer", "safest", "recommend", "suggest", "advise", "advisable", "best to", "better to",
                        "should follow", "go with", "prefer", "err on the side", "in the meantime"]
CHECKS = ["retrieval_hit", "tool_used", "figures", "citation", "abstention", "must_include", "no_recommendation",
          "post_reply_checks"]


def _check(ok: bool, reason: str) -> tuple[str, str]:
    return ("pass" if ok else "fail", reason)


def must_include(q: dict, answer: str):
    phrases = q.get("answer_must_include") or []
    if not phrases:
        return None
    have = set(tokens(answer))
    missing = [p for p in phrases if not set(tokens(p)) <= have]
    return _check(not missing, f"missing: {missing}" if missing else f"all {len(phrases)} phrases found")


def post_checks(out: dict):
    w = out.get("warnings") or []
    return _check(not w, "; ".join(w) if w else "no warnings")


def score_kb(q: dict, out: dict) -> dict:
    res, expected, bad = {}, set(q.get("expected_sources") or []), set(q.get("must_not_cite") or [])
    answerable = q["type"] != "abstain"
    returned = {s for s in out.get("sources_returned", []) if s.startswith("KB-")}
    cited = set(out.get("cited_kb", []))
    if answerable:
        hit = expected & returned
        res["retrieval_hit"] = _check(bool(hit), f"returned {sorted(hit)}" if hit else
                                      f"none of {sorted(expected)} returned; got {sorted(returned) or 'nothing'}")
    wrong = cited & bad
    if answerable:
        good = cited & expected
        ok = bool(good) and not wrong
        reason = (f"cites must_not_cite {sorted(wrong)}" if wrong else
                  f"cites {sorted(good)}" if good else f"cites none of {sorted(expected)}; cited {sorted(cited) or 'nothing'}")
    else:
        ok, reason = not wrong, (f"cites must_not_cite {sorted(wrong)}" if wrong else "no must_not_cite source cited")
    res["citation"] = _check(ok, reason)
    if answerable:
        res["abstention"] = _check(not out["declined"], "declined an answerable question" if out["declined"] else "answered")
    else:
        res["abstention"] = _check(out["declined"], "declined" if out["declined"] else "answered instead of declining")
    mi = must_include(q, out["answer"])
    if mi:
        res["must_include"] = mi
    if q["type"] == "conflict":
        found = [w for w in RECOMMENDATION_WORDS if w in out["answer"].lower()]
        res["no_recommendation"] = _check(not found, f"recommendation words: {found}" if found else "no recommendation words")
    res["post_reply_checks"] = post_checks(out)
    return res


def _path(result: dict, path: str):
    v = result
    for part in path.split("."):
        v = v[part]
    return v


def score_data(q: dict, out: dict, warehouse_db=None, alerts_db=None) -> dict:
    res, typ = {}, q["type"]
    names = [t["name"] for t in out.get("tools", [])]
    if typ in ("data", "definition"):
        tool = q["expected_tool"]
        res["tool_used"] = _check(tool in names, f"called {tool}" if tool in names else f"{tool} not called; called {names or 'nothing'}")
        truth = data_tools.call(tool, q.get("expected_args") or {}, warehouse_db=warehouse_db, alerts_db=alerts_db)
        if "error" in truth:
            raise RuntimeError(f"{q['id']}: expected tool failed: {truth['error']}")
        if q.get("checks"):
            in_answer = [v for _, v, _ in _numbers(out["answer"])]
            misses = []
            for c in q["checks"]:
                want = float(_path(truth, c["path"]))
                if not any(abs(v - want) <= c["tolerance"] + 1e-9 for v in in_answer):
                    misses.append(f"{c['path']}={want:g} not in answer (±{c['tolerance']})")
            res["figures"] = _check(not misses, "; ".join(misses) if misses else
                                    f"all {len(q['checks'])} figures within tolerance")
        sources = set(truth["sources"])
        cited = set(out.get("cited_views", []))
        res["citation"] = _check(bool(cited & sources), f"cites {sorted(cited & sources)}" if cited & sources else
                                 f"cites none of {sorted(sources)}; cited {sorted(cited) or 'nothing'}")
        res["abstention"] = _check(not out["declined"], "answered" if not out["declined"] else "declined an answerable question")
    else:
        res["abstention"] = _check(out["declined"], "declined" if out["declined"] else "answered instead of declining")
    mi = must_include(q, out["answer"])
    if mi:
        res["must_include"] = mi
    res["post_reply_checks"] = post_checks(out)
    return res


def passed(res: dict) -> bool:
    return all(v[0] == "pass" for k, v in res.items() if k not in ROUGH)


class _CacheOnly:
    """Stands in for the API client: any request not already cached ends the question."""
    def __init__(self):
        self.messages = self

    def create(self, **request):
        raise ApiFailed("Not in the response cache; --cache-only makes no API calls.")


def run(split: str, assistant: Assistant, out_dir: Path, final: bool = False) -> dict:
    questions = [("kb", q) for q in load(split, final=final)] + [("data", q) for q in load_data(split, final=final)]
    rows, spent = [], 0.0
    log = assistant.caller.log
    log_start = len(log.read_text(encoding="utf-8").splitlines()) if log.exists() else 0
    for n, (kind, q) in enumerate(questions, 1):
        if spent >= RUN_LIMIT_GBP:
            raise SystemExit(f"Run stopped: £{spent:.4f} spent, at the £{RUN_LIMIT_GBP:.2f} limit for one run.")
        out = assistant.ask(q["question"])
        spent += out["cost_gbp"]
        if out["stopped"]:
            res = {"abstention": ("fail", f"stopped: {out['answer']}")}
        else:
            res = score_kb(q, out) if kind == "kb" else score_data(q, out, assistant.warehouse_db, assistant.alerts_db)
        row = {"id": q["id"], "set": kind, "type": q["type"], "passed": "yes" if passed(res) else "no",
               "route": out["route"], "tools": " ".join(t["name"] for t in out["tools"])}
        for c in CHECKS:
            row[c], row[f"{c}_reason"] = res.get(c, ("n/a", ""))
        row.update({k: out[k] for k in ("api_calls", "cached_calls", "tokens_in", "tokens_out", "cost_gbp")})
        row["answer"] = out["answer"]
        rows.append(row)
        print(f"[{n:2d}/{len(questions)}] {q['id']:5s} {q['type']:12s} {'pass' if row['passed'] == 'yes' else 'FAIL'}  "
              f"API calls {out['api_calls']}, cached {out['cached_calls']}, £{spent:.4f} so far", flush=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"assistant_{split}.csv"
    with open(csv_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    new_log = log.read_text(encoding="utf-8").splitlines()[log_start:] if log.exists() else []
    entries = [json.loads(x) for x in new_log if x.strip()]
    summary = summarise(rows, split, entries)
    (out_dir / f"assistant_{split}_summary.md").write_text(summary, encoding="utf-8")
    return {"rows": rows, "summary": summary, "csv": csv_path}


def summarise(rows: list[dict], split: str, log_entries: list[dict] | None = None) -> str:
    model = yaml.safe_load(open(ROOT / "config" / "assistant.yaml", encoding="utf-8"))["model"]
    lines = [f"# Assistant evaluation: {split} questions", "",
             f"Model `{model}`. {len(rows)} questions. A question passes when every check that applies passes; "
             "rough keyword checks (must_include, no_recommendation) are reported separately and not counted.", "",
             "| Type | Questions | Passed |", "|---|---|---|"]
    by_type = defaultdict(list)
    for r in rows:
        by_type[(r["set"], r["type"])].append(r)
    for (s, t), rs in sorted(by_type.items()):
        lines.append(f"| {s} / {t} | {len(rs)} | {sum(r['passed'] == 'yes' for r in rs)} |")
    lines.append(f"| **All** | {len(rows)} | {sum(r['passed'] == 'yes' for r in rows)} |")
    lines += ["", "| Check | Pass | Fail | Not applicable |", "|---|---|---|---|"]
    for c in CHECKS:
        vals = [r[c] for r in rows]
        lines.append(f"| {c}{' (rough)' if c in ROUGH else ''} | {vals.count('pass')} | {vals.count('fail')} | {vals.count('n/a')} |")
    calls = sum(r["api_calls"] for r in rows) + sum(r["cached_calls"] for r in rows)
    lines += ["", "## Cost (uncached calls only)", "",
              f"- API calls made: {sum(r['api_calls'] for r in rows)}; served from the cache: "
              f"{sum(r['cached_calls'] for r in rows)} (of {calls})",
              f"- Tokens: {sum(r['tokens_in'] for r in rows):,} in, {sum(r['tokens_out'] for r in rows):,} out",
              f"- Cost: £{sum(r['cost_gbp'] for r in rows):.4f} (each dollar counted as a pound, D-037)"]
    if log_entries is not None:
        u = [e for e in log_entries if not e["cached"]]
        lines += [f"- From the call log for this run: {len(u)} API calls, {sum(e['input_tokens'] for e in u):,} tokens in, "
                  f"{sum(e['output_tokens'] for e in u):,} out, £{sum(e['cost_gbp'] for e in log_entries):.4f}. "
                  "Calls served from the cache here may have been paid for by an earlier, interrupted run; "
                  "see data/assistant/calls.jsonl or `python -m pipeline.assistant.ask --spend` for all spend."]
    lines += ["",
              "## Failures", ""]
    fails = [(r["id"], c, r[f"{c}_reason"]) for r in rows for c in CHECKS if r[c] == "fail"]
    lines += [f"- {i} {c}{' (rough)' if c in ROUGH else ''}: {why}" for i, c, why in fails] or ["- none"]
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--heldout", action="store_true")
    ap.add_argument("--cache-only", action="store_true")
    ap.add_argument("--out", type=Path, default=RESULTS)
    a = ap.parse_args()
    if a.heldout and not APPROVAL.exists():
        print("Refused: the held-out evaluation needs the project author's approval in chat, recorded in "
              f"{APPROVAL.relative_to(ROOT)}. No held-out file was opened.", file=sys.stderr)
        sys.exit(3)
    assistant = Assistant(client=_CacheOnly() if a.cache_only else None)
    out = run("heldout" if a.heldout else "tune", assistant, a.out, final=a.heldout)
    print(out["summary"])
    print(f"Rows: {out['csv']}")


if __name__ == "__main__":
    main()
