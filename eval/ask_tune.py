"""Ask the assistant one or more tuning questions by ID (step 5.4 live check).

Run:  python -m eval.ask_tune T04 DT04 DT08

Reads the question text from eval/questions/kb_eval_tune.yaml or data_eval_tune.yaml only (held-out files
are refused by eval.kb_questions). Each question makes real Claude API calls unless the identical request
is already cached; every call is logged to data/assistant/calls.jsonl.
"""
from __future__ import annotations

import json
import sys

from eval.kb_questions import load, load_data
from pipeline.assistant.ask import Assistant


def question(qid: str) -> str:
    if qid.startswith("DT"):
        return next(q for q in load_data("tune") if q["id"] == qid)["question"]
    return next(q for q in load("tune") if q["id"] == qid)["question"]


def main() -> None:
    a = Assistant()
    for qid in sys.argv[1:]:
        out = a.ask(question(qid))
        print(f"===== {qid}: {out['question']}")
        print(out["display"])
        print(f"[route: {out['route']}; tools: {json.dumps(out['tools'])}]")
        print(f"[API calls: {out['api_calls']} of {out['calls']}; cost £{out['cost_gbp']:.6f}; declined: {out['declined']}; "
              f"stopped: {out['stopped']}]")
        for w in out.get("warnings", []):
            print(f"[check: {w}]")
        print()


if __name__ == "__main__":
    main()
