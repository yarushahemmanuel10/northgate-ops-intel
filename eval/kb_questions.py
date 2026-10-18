"""Load the reviewer's evaluation questions (D-035, D-038).

load(split) gives the knowledge-base questions, load_data(split) the data-route questions. The held-out
files are refused unless final=True: they are used once, for the final evaluation, and must not be opened
while building or tuning (eval/questions/README.md). The check happens before a file is touched.
"""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
FILES = {"tune": ROOT / "eval" / "questions" / "kb_eval_tune.yaml",
         "heldout": ROOT / "eval" / "questions" / "kb_eval_heldout.yaml"}
DATA_FILES = {"tune": ROOT / "eval" / "questions" / "data_eval_tune.yaml",
              "heldout": ROOT / "eval" / "questions" / "data_eval_heldout.yaml"}


def _load(files: dict, split: str, final: bool) -> list[dict]:
    if split not in files:
        raise ValueError(f"unknown split {split!r}")
    if split == "heldout" and not final:
        raise PermissionError("the held-out questions are only for the final evaluation (pass final=True when asked to run it)")
    with open(files[split], encoding="utf-8") as f:
        return yaml.safe_load(f)["questions"]


def load(split: str = "tune", final: bool = False) -> list[dict]:
    return _load(FILES, split, final)


def load_data(split: str = "tune", final: bool = False) -> list[dict]:
    return _load(DATA_FILES, split, final)
