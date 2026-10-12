"""Build all synthetic data from scratch, in order.

Run:  python -m generator.run

1. Clean sources -> data/clean/ (and data/clean/ground_truth/), via the single generators.
2. Deliberate quality problems -> data/raw/ and data/ground_truth/ (generator.messiness,
   the only writer of data/raw/).
3. One-screen summary, and the README table of what is committed and what is generated.

Running it twice gives byte-identical files in all three data folders (tested).
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from generator.config import load_config

ROOT = Path(__file__).resolve().parents[1]

FILES_TABLE = """| Path | Committed? | What it is | Produced by |
|---|---|---|---|
| `config/generator.yaml` | Yes | Every generator setting (seed, volumes, rates) | Hand-written |
| `docs/`, `DECISIONS.md`, `README.md` | Yes | Business understanding, decisions, this file | Hand-written (this table by `generator.run`) |
| `generator/`, `shop_api/`, `tests/` | Yes | Code and tests | Hand-written |
| `knowledge_base/*.md`, `knowledge_base/_meta.yaml` | Yes | 50 KB documents and their ground truth | Hand-written (D-014) |
| `pyproject.toml`, `uv.lock`, `.python-version` | Yes | Exact dependency versions (D-005) | `uv` |
| `data/clean/` | No | Clean sources before any quality problems | `python -m generator.run` (step 1) |
| `data/raw/` | No | Messy sources the platform ingests | `python -m generator.run` (step 2) |
| `data/ground_truth/` | No | Answer keys: true accounts, known issues, KB links | `python -m generator.run` (step 2) |
| `data/staging/` | No | Ingested text-only tables with lineage | `python -m pipeline.ingest.run` |
| `data/validated/` | No | Typed tables and validation findings | `python -m pipeline.validate.run` |
| `.env` | No | API key (never committed) | You |

Everything under `data/` is reproduced exactly from the seed in `config/generator.yaml`."""


def build_all(cfg: dict, base: Path = ROOT, seed: int | None = None) -> dict:
    from generator import crm, helpdesk, messiness, shop, telecoms
    from generator.customers import write_ground_truth

    clean, raw, truth = base / "data" / "clean", base / "data" / "raw", base / "data" / "ground_truth"
    for d in (clean, raw, truth):
        if d.exists():
            shutil.rmtree(d)                     # from scratch: nothing left over from older runs
    clean_truth = clean / "ground_truth"
    write_ground_truth(cfg, clean_truth, seed=seed)
    crm.build(cfg, clean, seed=seed)
    helpdesk.build(cfg, clean, clean_truth, seed=seed)
    telecoms.build(cfg, clean, clean_truth, seed=seed)
    shop.build(cfg, clean, clean_truth, seed=seed)
    clean_tabs, mess, known, context, report = messiness.build(cfg, raw, truth, seed=seed)
    return {"clean": clean_tabs, "mess": mess, "known": known, "context": context, "report": report,
            "dirs": (clean, raw, truth)}


def update_readme(path: Path = ROOT / "README.md") -> None:
    text = path.read_text(encoding="utf-8")
    block = f"<!-- files-table:start -->\n{FILES_TABLE}\n<!-- files-table:end -->"
    if "<!-- files-table:start -->" in text:
        text = re.sub(r"<!-- files-table:start -->.*?<!-- files-table:end -->", block, text, flags=re.S)
    else:
        text = text.rstrip() + "\n\n## What is committed and what is generated\n\n" + block + "\n"
    path.write_text(text, encoding="utf-8")


def summary(cfg: dict, out: dict) -> str:
    from generator.messiness import revenue_check
    from generator.patterns import compare
    clean, mess, known, context = out["clean"], out["mess"], out["known"], out["context"]
    lines = ["Northgate synthetic data (fictional company) - build summary", ""]
    for d in out["dirs"]:
        files = sorted(p for p in d.rglob("*") if p.is_file())
        lines.append(f"{d.relative_to(d.parents[1])}/: {len(files)} files")
    lines.append("")
    lines.append("Rows (clean -> raw): " + ", ".join(
        f"{n} {len(clean[n].rows)}->{len(mess.tables[n].rows)}" for n in mess.tables))
    by_qi = known[known.op != "pii_span"].groupby("qi").size()
    lines.append("Logged changes by QI: " + ", ".join(f"{q} {n}" for q, n in by_qi.items())
                 + f"; personal-data spans {int((known.op == 'pii_span').sum())}")
    rc = revenue_check(clean, mess.tables, known).set_index(["line", "view"]).revenue
    lines.append(f"BL2 revenue: clean £{rc[('BL2', 'clean (before any mess)')]:,.0f}, raw as exported "
                 f"£{rc[('BL2', 'messy, summed as exported')]:,.0f}, correctly cleaned "
                 f"£{rc[('BL2', 'messy, correctly cleaned (duplicates removed, credits applied)')]:,.0f}")
    lines.append(f"BL5 revenue: clean £{rc[('BL5', 'clean (before any mess)')]:,.0f}, raw as exported "
                 f"£{rc[('BL5', 'messy, summed as exported')]:,.0f}, correctly cleaned "
                 f"£{rc[('BL5', 'messy, correctly cleaned')]:,.0f}")
    pat = compare(cfg, clean, mess.tables, known, context)
    risk = set(pat["risk"])
    for mode in ("clean", "cleaned", "naive"):
        f = pat["flagged"][mode]
        lines.append(f"BQ-06 pattern, {mode:7s}: {len(f):2d} flagged, {len(risk & set(f))} of {len(risk)} at-risk, "
                     f"healthy {sorted(set(f) - risk)}")
    return "\n".join(lines)


def main() -> None:
    cfg = load_config()
    out = build_all(cfg)
    update_readme()
    print(summary(cfg, out))


if __name__ == "__main__":
    main()
