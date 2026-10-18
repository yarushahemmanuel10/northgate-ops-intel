"""Retrieval report on the tuning questions (step 5.2, D-035).

Run:  python -m eval.retrieval_eval   (after pipeline.retrieval.index)

For each tuning question: its type, whether any expected source is in the top 3 articles, the top 3
returned, any must_not_cite article in the top 5, and the top score (for a later "too weak to
answer" cut-off). Hit rate by type. Never touches the held-out file.
"""
from __future__ import annotations

import statistics
from collections import defaultdict

from eval.kb_questions import load
from pipeline.retrieval.index import search


def report(questions: list[dict], k_hit: int = 3, k_bad: int = 5) -> dict:
    rows = []
    for q in questions:
        res = search(q["question"], k=max(k_hit, k_bad))
        top = [r["kb_id"] for r in res]
        expected = set(q.get("expected_sources") or [])
        rows.append({"id": q["id"], "type": q["type"], "expected": sorted(expected),
                     "hit": bool(expected & set(top[:k_hit])) if expected else None,
                     "top3": top[:3], "top5": top[:5],
                     "all_expected_in_top5": expected <= set(top[:k_bad]) if expected else None,
                     "bad_in_top5": sorted(set(q.get("must_not_cite") or []) & set(top[:k_bad])),
                     "top_score": res[0]["score"] if res else 0.0})
    by_type = defaultdict(list)
    for r in rows:
        if r["hit"] is not None:
            by_type[r["type"]].append(r["hit"])
    answerable = [r["top_score"] for r in rows if r["type"] != "abstain"]
    abstain = [r["top_score"] for r in rows if r["type"] == "abstain"]
    return {"rows": rows, "hit_rate_by_type": {t: (sum(v), len(v)) for t, v in sorted(by_type.items())},
            "hit_rate": (sum(r["hit"] for r in rows if r["hit"] is not None), sum(r["hit"] is not None for r in rows)),
            "answerable_scores": sorted(answerable), "abstain_scores": sorted(abstain),
            "kb0026_returned": any("KB-0026" in r["top5"] for r in rows)}


def main() -> None:
    out = report(load("tune"))
    print(f"{'id':8} {'type':11} {'hit@3':5}  top 3 returned           expected")
    for r in out["rows"]:
        hit = "-" if r["hit"] is None else ("yes" if r["hit"] else "NO")
        bad = f"  must_not_cite in top 5: {r['bad_in_top5']}" if r["bad_in_top5"] else ""
        print(f"{r['id']:8} {r['type']:11} {hit:5}  {', '.join(r['top3']):24} {', '.join(r['expected']) or '(none)'}"
              f"  top score {r['top_score']:.2f}{bad}")
    h, n = out["hit_rate"]
    print(f"\nHit rate @3 (answerable questions): {h}/{n}")
    for t, (h, n) in out["hit_rate_by_type"].items():
        print(f"  {t}: {h}/{n}")
    a, s = out["answerable_scores"], out["abstain_scores"]
    print(f"Top score, answerable questions: min {min(a):.2f}, median {statistics.median(a):.2f}, max {max(a):.2f}")
    print(f"Top score, abstain questions: {', '.join(f'{x:.2f}' for x in s)}")
    multi = [r for r in out["rows"] if r["type"] == "multi_doc"]
    print(f"multi_doc questions with every expected source in the top 5: {sum(r['all_expected_in_top5'] for r in multi)}/{len(multi)}")
    print(f"KB-0026 (superseded) returned for any question: {out['kb0026_returned']}")


if __name__ == "__main__":
    main()
