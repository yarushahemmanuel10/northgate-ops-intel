"""Pipeline settings from config/pipeline.yaml."""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def load_settings(path: Path = ROOT / "config" / "pipeline.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        s = yaml.safe_load(f)
    for key in ("raw_dir", "kb_dir", "staging_dir", "validated_dir", "resolved_dir"):
        s[key] = ROOT / s[key]
    return s
