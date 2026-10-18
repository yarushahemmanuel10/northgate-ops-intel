"""Pipeline settings from config/pipeline.yaml."""
from __future__ import annotations

import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def load_settings(path: Path = ROOT / "config" / "pipeline.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        s = yaml.safe_load(f)
    if os.environ.get("NORTHGATE_SHOP_API_URL"):             # Docker compose: http://shop_api:8765 (D-046)
        s["shop_api"]["base_url"] = os.environ["NORTHGATE_SHOP_API_URL"]
    for key in ("raw_dir", "kb_dir", "staging_dir", "validated_dir", "resolved_dir", "warehouse_dir", "alerts_dir", "retrieval_dir", "assistant_dir", "audit_dir"):
        s[key] = ROOT / s[key]
    return s
