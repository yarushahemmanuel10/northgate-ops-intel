"""Knowledge-base index and search (step 5.2, D-035).

Build:  python -m pipeline.retrieval.index   (after pipeline.validate.run)
Search: from pipeline.retrieval.index import search; search("question", k=5)

Reads data/validated/kb_documents.csv only. Each current article is split into its `##` sections;
each section is indexed with its article title and heading in front, so a short section still
matches on the article's subject. Superseded articles are not indexed; they are listed with their
replacement so the assistant can say an article was replaced. Near-duplicate articles are kept as
separate results (they may disagree). Writes data/retrieval/kb_index.duckdb, rebuilt each run.
"""
from __future__ import annotations

import csv
import os
import re
import sys
from functools import lru_cache
from pathlib import Path

import duckdb

from pipeline.retrieval.bm25 import BM25
from pipeline.retrieval.text import tokens
from pipeline.settings import load_settings

K1, B = 1.5, 0.75
_HEADING = re.compile(r"^## +(.+)$", re.M)


def sections(body: str) -> list[tuple[str, str]]:
    """(heading, text) for each `##` section; the title line and the synthetic notice are skipped."""
    parts = _HEADING.split(body)
    out = []
    for i in range(1, len(parts), 2):
        text = parts[i + 1].strip()
        if text:
            out.append((parts[i].strip(), text))
    return out


def build(settings: dict, validated: Path | None = None, out_dir: Path | None = None) -> dict:
    with open((validated or settings["validated_dir"]) / "kb_documents.csv", encoding="utf-8", newline="") as f:
        docs = list(csv.DictReader(f))
    rows, superseded = [], []
    for d in sorted(docs, key=lambda r: r["id"]):
        if d["status"] == "superseded":
            superseded.append((d["id"], d["title"], d["superseded_by"] or None, d["superseded_on"] or None))
            continue
        for n, (heading, text) in enumerate(sections(d["body"]), start=1):
            indexed = f"{d['title']}. {heading}. {text}"
            rows.append((f"{d['id']}#{n}", d["id"], d["title"], d["type"], heading, text, indexed))
    out = (out_dir or settings["retrieval_dir"])
    out.mkdir(parents=True, exist_ok=True)
    path = out / "kb_index.duckdb"
    tmp = path.with_suffix(".building.duckdb")
    tmp.unlink(missing_ok=True)
    con = duckdb.connect(str(tmp))
    try:
        con.execute("CREATE TABLE kb_section (section_id VARCHAR, kb_id VARCHAR, title VARCHAR, doc_type VARCHAR, "
                    "heading VARCHAR, text VARCHAR, indexed_text VARCHAR)")
        con.executemany("INSERT INTO kb_section VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
        con.execute("CREATE TABLE kb_superseded (kb_id VARCHAR, title VARCHAR, superseded_by VARCHAR, superseded_on DATE)")
        con.executemany("INSERT INTO kb_superseded VALUES (?, ?, ?, ?)", superseded)
    finally:
        con.close()
    os.replace(tmp, path)
    _load.cache_clear()
    return {"path": str(path), "articles": len({r[1] for r in rows}), "sections": len(rows), "superseded": len(superseded)}


@lru_cache(maxsize=4)
def _load(path: str):
    con = duckdb.connect(path, read_only=True)
    try:
        secs = con.execute("SELECT section_id, kb_id, title, heading, text, indexed_text FROM kb_section "
                           "ORDER BY section_id").fetchall()
        sup = con.execute("SELECT kb_id, title, superseded_by FROM kb_superseded").fetchall()
    finally:
        con.close()
    return secs, BM25([tokens(s[5]) for s in secs], K1, B), sup


def search(query: str, k: int = 5, index_path: Path | None = None) -> list[dict]:
    """Top k articles for a query, each with its best-matching section and BM25 score."""
    path = index_path or load_settings()["retrieval_dir"] / "kb_index.duckdb"
    secs, bm25, _ = _load(str(path))
    scores = bm25.scores(tokens(query))
    best: dict[str, tuple[float, tuple]] = {}
    for s, sc in zip(secs, scores):
        if sc > 0 and (s[1] not in best or sc > best[s[1]][0]):
            best[s[1]] = (sc, s)
    ranked = sorted(best.items(), key=lambda kv: (-kv[1][0], kv[0]))[:k]
    return [{"kb_id": kb, "title": s[2], "section_id": s[0], "heading": s[3], "text": s[4], "score": sc}
            for kb, (sc, s) in ranked]


def superseded(index_path: Path | None = None) -> list[dict]:
    path = index_path or load_settings()["retrieval_dir"] / "kb_index.duckdb"
    return [{"kb_id": a, "title": t, "superseded_by": b} for a, t, b in _load(str(path))[2]]


def main() -> None:
    out = build(load_settings())
    print(f"Indexed {out['sections']} sections from {out['articles']} current articles "
          f"({out['superseded']} superseded left out) in {out['path']}")
    if len(sys.argv) > 1:
        for r in search(" ".join(sys.argv[1:])):
            print(f"  {r['score']:6.2f}  {r['kb_id']}  {r['title']} / {r['heading']}")


if __name__ == "__main__":
    main()
