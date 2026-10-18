"""Load the reviewer's knowledge-base questions (D-035).

The held-out file is refused unless final=True: it is used once, for the final evaluation, and
must not be opened while building or tuning (eval/questions/README.md). The check happens before
the file is touched.
"""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
FILES = {"tune": ROOT / "eval" / "questions" / "kb_eval_tune.yaml",
         "heldout": ROOT / "eval" / "questions" / "kb_eval_heldout.yaml"}


def load(split: str = "tune", final: bool = False) -> list[dict]:
    if split not in FILES:
        raise ValueError(f"unknown split {split!r}")
    if split == "heldout" and not final:
        raise PermissionError("the held-out questions are only for the final evaluation (pass final=True when asked to run it)")
    with open(FILES[split], encoding="utf-8") as f:
        return yaml.safe_load(f)["questions"]
