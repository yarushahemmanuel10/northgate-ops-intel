"""Evaluate ticket-text masking against the answer key (step 6.1, D-043).

Run:  python -m eval.masking_eval            # dev half: recall and false masks per type, with every miss and false mask
      python -m eval.masking_eval --final    # held-out half, once: writes eval/results/masking_heldout/ and refuses again

Tickets are split into dev and held-out halves by the parity of an MD5 hash of the ticket ID (as in entity
resolution, D-021), so the split is fixed with no seed. Truth is the QI-07 `pii_span` entries in
data/ground_truth/known_issues.csv (personal data planted in ticket descriptions: name, phone, email). Only this
evaluation code reads the answer key; the masker uses general rules and the warehouse's own lists.

  recall      a true span counts as caught when a masked span overlaps it (whatever type it was given; a wrong
              type is counted separately)
  false mask  a masked span in a description that overlaps no true span, by the type it was given
CUSTOMER masks (business names) have no truth in the answer key and are counted separately. Masks in
resolution_notes are counted separately too: the answer key records no personal data there.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import sys
from collections import Counter, defaultdict
from pathlib import Path

import duckdb

from eval.kb_questions import ROOT
from pipeline.mask.run import lists_from
from pipeline.mask.text import find

KEY = ROOT / "data" / "ground_truth" / "known_issues.csv"
FINAL_DIR = ROOT / "eval" / "results" / "masking_heldout"
KINDS = {"pii:name": "NAME", "pii:phone": "PHONE", "pii:email": "EMAIL"}


def split(ticket_id: str) -> str:
    return "dev" if int(hashlib.md5(ticket_id.encode()).hexdigest(), 16) % 2 == 0 else "held-out"


def truth() -> dict[str, list[tuple[str, str]]]:
    out = defaultdict(list)
    with open(KEY, encoding="utf-8", newline="") as fh:
        next(fh)                                                   # synthetic-data banner line
        for r in csv.DictReader(fh):
            if r["qi"] == "QI-07" and r["op"] == "pii_span" and r["field"] == "description":
                out[r["record_key"]].append((KINDS[r["note"]], r["new_value"]))
    return out


def score(which: str, warehouse_db: Path) -> dict:
    con = duckdb.connect(str(warehouse_db), read_only=True)
    try:
        lists = lists_from(con)
        texts = con.execute("SELECT ticket_id, description, resolution_notes FROM ticket_text").fetchall()
    finally:
        con.close()
    key = truth()
    caught, total, wrong_type, false, cust, notes = Counter(), Counter(), Counter(), Counter(), 0, Counter()
    misses, falses = [], []
    for tid, desc, res in texts:
        if split(tid) != which:
            continue
        desc = desc or ""
        spans = find(desc, lists)
        true_spans, used = [], set()
        for kind, value in key.get(tid, []):
            start = desc.find(value)
            while start in used and start != -1:
                start = desc.find(value, start + 1)
            if start == -1:
                raise RuntimeError(f"{tid}: answer-key span not found in the description")
            used.add(start)
            true_spans.append((start, start + len(value), kind, value))
        for s, e, kind, value in true_spans:
            total[kind] += 1
            hit = [p for p in spans if p.start < e and s < p.end]
            if hit:
                caught[kind] += 1
                if all(p.kind != kind for p in hit):
                    wrong_type[kind] += 1
            else:
                misses.append((tid, kind, value, desc))
        for p in spans:
            if p.kind == "CUSTOMER":
                cust += 1
            elif not any(p.start < e and s < p.end for s, e, _, _ in true_spans):
                false[p.kind] += 1
                falses.append((tid, p.kind, p.rule, desc[p.start:p.end], desc))
        for p in find(res or "", lists):
            notes[p.kind] += 1
    return {"split": which, "caught": caught, "total": total, "wrong_type": wrong_type, "false": false,
            "customer_masks": cust, "notes_masks": notes, "misses": misses, "falses": falses,
            "tickets": sum(split(t) == which for t, _, _ in texts)}


def report(r: dict, show_examples: bool) -> str:
    lines = [f"# Masking evaluation: {r['split']} half ({r['tickets']} tickets)", "",
             "| Type | True spans | Caught | Recall | Caught with the wrong type | False masks |", "|---|---|---|---|---|---|"]
    for k in ("NAME", "PHONE", "EMAIL"):
        t, c = r["total"][k], r["caught"][k]
        lines.append(f"| {k.lower()} | {t} | {c} | {c / t:.1%} | {r['wrong_type'][k]} | {r['false'][k]} |" if t else
                     f"| {k.lower()} | 0 | 0 | - | 0 | {r['false'][k]} |")
    T, C = sum(r["total"].values()), sum(r["caught"].values())
    lines += [f"| **all** | {T} | {C} | {C / T:.1%} | {sum(r['wrong_type'].values())} | {sum(r['false'].values())} |", "",
              f"- Business-name masks (no truth in the answer key): {r['customer_masks']}",
              f"- Masks in resolution notes (the answer key records no personal data there): "
              f"{dict(r['notes_masks']) or 'none'}", "",
              f"## Missed spans ({len(r['misses'])})", ""]
    lines += [f"- {t} {k.lower()}: `{v}`" + (f" in: {d}" if show_examples else "") for t, k, v, d in r["misses"]] or ["- none"]
    lines += ["", f"## False masks ({len(r['falses'])})", ""]
    lines += [f"- {t} {k.lower()} ({rule}): `{v}`" + (f" in: {d}" if show_examples else "") for t, k, rule, v, d in r["falses"]] or ["- none"]
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--final", action="store_true", help="score the held-out half, once")
    a = ap.parse_args()
    db = ROOT / "data" / "warehouse" / "northgate.duckdb"
    if a.final:
        if (FINAL_DIR / "summary.md").exists():
            print(f"Refused: the held-out masking evaluation has already been run ({FINAL_DIR.relative_to(ROOT)}).",
                  file=sys.stderr)
            sys.exit(3)
        r = score("held-out", db)
        FINAL_DIR.mkdir(parents=True, exist_ok=True)
        text = report(r, show_examples=False)
        (FINAL_DIR / "summary.md").write_text(text, encoding="utf-8")
        print(text)
        return
    print(report(score("dev", db), show_examples=True))


if __name__ == "__main__":
    main()
