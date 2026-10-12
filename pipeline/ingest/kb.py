"""Connector for the knowledge base: Markdown files with YAML front matter.

Files whose names start with "_" are not documents and are not read: the KB's meta file
holds answers about the KB (which articles are near-duplicates) and is off limits (D-017).
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import yaml

from pipeline.ingest.common import LINEAGE, Rejected, SchemaError
from pipeline.ingest.schemas import KB_FIELDS, KB_OPTIONAL

KB_COLUMNS = KB_FIELDS + KB_OPTIONAL + ["body"] + LINEAGE


def _text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    if isinstance(v, list):
        return ";".join(str(x) for x in v)
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def read_kb(kb_dir: Path, ingested_at: str, rejected: Rejected) -> list[dict]:
    out = []
    for path in sorted(kb_dir.glob("*.md")):
        if path.name.startswith("_"):
            continue
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---\n") or text.count("---\n") < 2:
            rejected.add("knowledge_base", path.name, "1", "no front matter", text[:200])
            continue
        _, front, body = text.split("---\n", 2)
        try:
            meta = yaml.safe_load(front)
        except yaml.YAMLError as exc:
            rejected.add("knowledge_base", path.name, "1", f"front matter is not valid YAML: {exc}", front)
            continue
        missing = [f for f in KB_FIELDS if f not in meta]
        if missing:
            raise SchemaError(f"{path.name}: front matter missing {missing}")
        extra = [f for f in meta if f not in KB_FIELDS + KB_OPTIONAL]
        if extra:
            raise SchemaError(f"{path.name}: unexpected front matter fields {extra}")
        rec = {f: _text(meta.get(f)) for f in KB_FIELDS + KB_OPTIONAL}
        rec["body"] = body
        rec.update(dict(zip(LINEAGE, ("knowledge_base", path.name, "1", ingested_at))))
        out.append(rec)
    return out
