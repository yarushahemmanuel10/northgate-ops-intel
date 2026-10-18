"""Append-only audit log with a hash chain (step 6.4, D-045).

    record(action, role=, session=, target=, outcome=, detail=)   append one entry
    write_head()                                                  anchor: copy the head hash to head.txt
    verify()                                                      check the chain, the anchor and retention

Each entry is one JSON line: seq, time (UTC), session, role, action, target, outcome, detail, prev (the hash of
the previous entry) and hash (SHA-256 of the entry without its own hash). Changing, removing or reordering an
entry breaks the chain from that point. head.txt, written at the end of every pipeline run, holds the head hash at
that moment, so cutting entries off the end is caught too. Limit: someone with write access to both files can
rewrite the whole log consistently; production would use write-once storage.

The log records demo roles and random session IDs, not people: there is no sign-in. Every string written is first
passed through the ticket-text masker with the warehouse's own name lists, so no business name, staff name, email
or phone number reaches the log even if a caller passes one.
"""
from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import json
import os
import secrets
from functools import lru_cache
from pathlib import Path

import yaml

from pipeline.settings import ROOT, load_settings

LOG_NAME, HEAD_NAME = "audit.jsonl", "head.txt"
GENESIS = "0" * 64


def audit_dir() -> Path:
    """NORTHGATE_AUDIT_DIR (tests, other environments), else audit_dir from config/pipeline.yaml."""
    return Path(os.environ.get("NORTHGATE_AUDIT_DIR") or load_settings()["audit_dir"])


def new_session() -> str:
    return secrets.token_hex(8)


def retention_days() -> int:
    with open(ROOT / "config" / "audit.yaml", encoding="utf-8") as fh:
        return int(yaml.safe_load(fh)["retention_days"])


@lru_cache(maxsize=4)
def _lists(warehouse_db: str):
    from pipeline.mask.text import Lists
    if not Path(warehouse_db).exists():
        return Lists()
    import duckdb
    from pipeline.mask.run import lists_from
    con = duckdb.connect(warehouse_db, read_only=True)
    try:
        return lists_from(con)
    finally:
        con.close()


def _clean(value, lists):
    from pipeline.mask.text import mask
    if isinstance(value, str):
        return mask(value, lists)[0]
    if isinstance(value, dict):
        return {k: _clean(v, lists) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v, lists) for v in value]
    return value


def _hash(entry: dict) -> str:
    body = {k: v for k, v in entry.items() if k != "hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _last(path: Path) -> dict | None:
    if not path.exists() or path.stat().st_size == 0:
        return None
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        pos = fh.tell() - 1
        while pos > 0:                                   # read back to the start of the last line
            fh.seek(pos - 1)
            if fh.read(1) == b"\n":
                break
            pos -= 1
        fh.seek(max(pos, 0))
        return json.loads(fh.read().decode())


def record(action: str, *, role: str, session: str, target: str | None = None, outcome: str = "ok",
           detail: dict | None = None, directory: Path | None = None, warehouse_db: Path | None = None) -> dict:
    d = Path(directory or audit_dir())
    d.mkdir(parents=True, exist_ok=True)
    path = d / LOG_NAME
    if warehouse_db is None:
        from pipeline.warehouse.load import load_config
        warehouse_db = load_settings()["warehouse_dir"] / load_config()["database"]
    lists = _lists(str(warehouse_db))
    with open(path, "a", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)                   # several dashboard sessions may append at once
        try:
            last = _last(path)
            entry = {"seq": (last["seq"] + 1) if last else 1,
                     "time": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                     "session": session, "role": role, "action": action,
                     "target": _clean(target, lists), "outcome": outcome, "detail": _clean(detail or {}, lists),
                     "prev": last["hash"] if last else GENESIS}
            entry["hash"] = _hash(entry)
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            fh.flush()
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
    return entry


def write_head(directory: Path | None = None) -> str | None:
    d = Path(directory or audit_dir())
    last = _last(d / LOG_NAME)
    if last is None:
        return None
    (d / HEAD_NAME).write_text(f"{last['seq']} {last['hash']} {last['time']}\n", encoding="utf-8")
    return last["hash"]


def verify(directory: Path | None = None, now: dt.datetime | None = None) -> dict:
    d = Path(directory or audit_dir())
    path, head = d / LOG_NAME, d / HEAD_NAME
    out = {"entries": 0, "ok": True, "problems": [], "head": GENESIS, "anchor": None, "by_action": {},
           "older_than_retention": 0, "retention_days": retention_days()}
    if not path.exists():
        return out
    now = now or dt.datetime.now(dt.timezone.utc)
    cutoff = now - dt.timedelta(days=out["retention_days"])
    prev, hashes = GENESIS, {}
    with open(path, encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                out["problems"].append(f"line {n}: not valid JSON")
                break
            if e.get("prev") != prev:
                out["problems"].append(f"line {n} (seq {e.get('seq')}): does not follow the previous entry")
                break
            if _hash(e) != e.get("hash"):
                out["problems"].append(f"line {n} (seq {e.get('seq')}): contents changed since it was written")
                break
            if e.get("seq") != n:
                out["problems"].append(f"line {n}: sequence number {e.get('seq')} out of order")
                break
            prev = e["hash"]
            hashes[e["seq"]] = e["hash"]
            out["entries"] = n
            out["by_action"][e["action"]] = out["by_action"].get(e["action"], 0) + 1
            if dt.datetime.fromisoformat(e["time"]) < cutoff:
                out["older_than_retention"] += 1
    out["head"] = prev
    if head.exists():
        seq, h, when = head.read_text(encoding="utf-8").split()
        out["anchor"] = {"seq": int(seq), "hash": h, "time": when}
        if hashes.get(int(seq)) != h:
            out["problems"].append(f"head.txt (seq {seq}, {when}) does not match the log: entries were removed or rewritten")
    out["ok"] = not out["problems"]
    return out


def main() -> None:
    import sys
    r = verify()
    print(f"Audit log: {audit_dir() / LOG_NAME}")
    print(f"Entries: {r['entries']}; chain {'intact' if r['ok'] else 'BROKEN'}")
    print(f"Head hash: {r['head']}")
    a = r["anchor"]
    print(f"Anchor (head.txt): {'seq ' + str(a['seq']) + ' at ' + a['time'] if a else 'none yet (written by each pipeline run)'}")
    print(f"Older than the {r['retention_days']}-day retention period: {r['older_than_retention']} "
          "(reported only; nothing is deleted automatically)")
    for k, v in sorted(r["by_action"].items()):
        print(f"  {k}: {v}")
    for p in r["problems"]:
        print(f"PROBLEM: {p}")
    sys.exit(0 if r["ok"] else 1)
