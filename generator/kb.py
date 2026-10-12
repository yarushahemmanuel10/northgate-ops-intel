"""Load the knowledge base (knowledge_base/*.md) and its ground-truth notes (_meta.yaml)."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
KB_DIR = ROOT / "knowledge_base"
REQUIRED_FIELDS = ("id", "title", "type", "topic_ids", "author", "created", "updated", "status",
                   "superseded_by")


def parse(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"{path.name}: missing front matter")
    _, fm, body = text.split("---\n", 2)
    meta = yaml.safe_load(fm)
    meta["body"] = body
    meta["path"] = path
    return meta


def load_kb(kb_dir: Path = KB_DIR) -> list[dict]:
    return [parse(p) for p in sorted(kb_dir.glob("*.md")) if not p.name.startswith("_")]


def load_meta(kb_dir: Path = KB_DIR) -> dict:
    with open(kb_dir / "_meta.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def available_on(doc: dict, day: dt.date) -> bool:
    """Was this article the one to use on `day`? (Created by then, and not yet superseded.)"""
    if doc["created"] > day:
        return False
    if doc["status"] == "superseded":
        return day < doc["superseded_on"]
    return True


def word_count(doc: dict) -> int:
    return len(doc["body"].split())


def main() -> None:
    import statistics
    docs = load_kb()
    counts = sorted(word_count(d) for d in docs)
    by_type = {t: sorted(word_count(d) for d in docs if d["type"] == t) for t in ("article", "policy")}
    print(f"{len(docs)} documents: {len(by_type['article'])} articles, {len(by_type['policy'])} policies")
    print(f"Words per document (body): min {counts[0]}, median {statistics.median(counts):.0f}, max {counts[-1]}")
    for t, c in by_type.items():
        print(f"  {t}: min {c[0]}, median {statistics.median(c):.0f}, max {c[-1]}")
    meta = load_meta()
    for s in meta["superseded"]:
        print(f"Superseded: {s['old']} -> {s['current']} on {s['superseded_on']}")
    for p in meta["near_duplicates"]:
        print(f"Near-duplicate {p['pair'][0]} / {p['pair'][1]} ({p['topic']}): {'agree' if p['agree'] else 'DISAGREE'}")


if __name__ == "__main__":
    main()
