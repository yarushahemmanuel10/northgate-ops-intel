"""The knowledge-base search tool the assistant calls (D-035, D-037).

Returns the top articles' best sections (KB text only), a weak_evidence flag when the top score is below the
configured level, and a conflict warning when a known conflicting article is involved. Superseded articles
are not in the index, so they can never be returned or cited.
"""
from __future__ import annotations

from pathlib import Path

from pipeline.retrieval import index as kb

TOOL = {
    "name": "search_knowledge_base",
    "description": "Search Northgate's IT support knowledge base (procedures and policies); returns the best-matching article sections.",
    "input_schema": {"type": "object", "additionalProperties": False, "required": ["query"],
                     "properties": {"query": {"type": "string", "minLength": 2, "maxLength": 300,
                                              "description": "What to look for, in plain words."}}},
}


def search(query, cfg: dict, index_path: Path | None = None) -> dict:
    if not isinstance(query, str) or not 2 <= len(query.strip()) <= 300:
        return {"error": {"code": "bad_parameter", "message": "query must be text of 2 to 300 characters.",
                          "parameter": "query"}}
    k = cfg["top_k"]
    wide = kb.search(query, k=50, index_path=index_path)
    top = wide[:k]
    ids = {r["kb_id"] for r in top}
    warnings = []
    for pair in cfg.get("conflicts", []):
        found = [p for p in pair if p in ids]
        if not found:
            continue
        for p in pair:                                 # bring in the partner article if it was not in the top k
            if p not in ids:
                extra = next((r for r in wide if r["kb_id"] == p), None)
                if extra:
                    top.append(extra)
                    ids.add(p)
        warnings.append(f"{' and '.join(pair)} are current articles known to disagree on parts of this topic: "
                        "compare them, show both positions with their citations, and flag the conflict.")
    best = top[0]["score"] if top else 0.0
    return {"tool": "search_knowledge_base", "sources": sorted(ids),
            "weak_evidence": best < cfg["weak_score"],
            "results": [{"kb_id": r["kb_id"], "title": r["title"], "heading": r["heading"], "text": r["text"],
                         "score": round(r["score"], 2)} for r in top],
            "warnings": warnings,
            "note": "Scores are keyword-match strength. A high score does not prove the sections answer the question."}
