"""Load and validate config/generator.yaml.

Validation catches internal mistakes (rates outside 0-1, mixes that don't sum to 1,
dates out of order). Agreement with the fact sheet is checked in tests/test_config.py.
"""
from __future__ import annotations

import datetime as dt
import math
from pathlib import Path
from typing import Any

import yaml

DEFAULT_PATH = Path(__file__).resolve().parents[1] / "config" / "generator.yaml"
PRIORITIES = ("P1", "P2", "P3", "P4")
BUSINESS_LINES = ("BL1", "BL2", "BL3", "BL4", "BL5")


class ConfigError(ValueError):
    """Raised when the generator config is invalid."""


def load_config(path: Path | str = DEFAULT_PATH) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    validate(cfg)
    return cfg


def _check_share(name: str, value: Any) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 1:
        raise ConfigError(f"{name} must be a number between 0 and 1, got {value!r}")


def _check_sums_to_one(name: str, mapping: dict[str, float]) -> None:
    for k, v in mapping.items():
        _check_share(f"{name}.{k}", v)
    total = sum(mapping.values())
    if not math.isclose(total, 1.0, abs_tol=1e-9):
        raise ConfigError(f"{name} must sum to 1, got {total}")


def _walk_rates(prefix: str, node: Any) -> None:
    """Every key ending in '_rate' or '_share' anywhere in the tree must be in [0, 1]."""
    if isinstance(node, dict):
        for k, v in node.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            if str(k).endswith(("_rate", "_share")) and not isinstance(v, dict):
                _check_share(path, v)
            _walk_rates(path, v)


def validate(cfg: dict[str, Any]) -> None:
    for key in ("seed", "window", "as_of_date", "volumes", "business_line_uptake",
                "revenue_share_targets", "tickets", "at_risk_customers", "quality_issues"):
        if key not in cfg:
            raise ConfigError(f"missing top-level key: {key}")

    start, end, as_of = cfg["window"]["start"], cfg["window"]["end"], cfg["as_of_date"]
    for name, d in (("window.start", start), ("window.end", end), ("as_of_date", as_of)):
        if not isinstance(d, dt.date):
            raise ConfigError(f"{name} must be a YYYY-MM-DD date, got {d!r}")
    if start >= end:
        raise ConfigError("window.start must be before window.end")
    if as_of != end:
        raise ConfigError("as_of_date must equal window.end (D-002)")

    v = cfg["volumes"]
    for k, n in v.items():
        if not isinstance(n, int) or isinstance(n, bool) or n < 0:
            raise ConfigError(f"volumes.{k} must be a non-negative integer, got {n!r}")
    if v["new_customers_in_window"] + v["closed_customers"] > v["customers"]:
        raise ConfigError("new + closed customers cannot exceed total customers")
    if v["open_tickets_at_as_of"] > v["tickets"]:
        raise ConfigError("open tickets cannot exceed total tickets")
    if v["kb_superseded"] > v["kb_articles"]:
        raise ConfigError("superseded articles cannot exceed total articles")

    if set(cfg["business_line_uptake"]) != set(BUSINESS_LINES):
        raise ConfigError(f"business_line_uptake must list exactly {BUSINESS_LINES}")
    for k, s in cfg["business_line_uptake"].items():
        _check_share(f"business_line_uptake.{k}", s)
    if set(cfg["revenue_share_targets"]) != set(BUSINESS_LINES):
        raise ConfigError(f"revenue_share_targets must list exactly {BUSINESS_LINES}")
    _check_sums_to_one("revenue_share_targets", cfg["revenue_share_targets"])

    t = cfg["tickets"]
    for key in ("priority_mix", "sla_hours", "resolution_breach_rate"):
        if set(t[key]) != set(PRIORITIES):
            raise ConfigError(f"tickets.{key} must list exactly {PRIORITIES}")
    _check_sums_to_one("tickets.priority_mix", t["priority_mix"])
    for p, h in t["sla_hours"].items():
        if not 0 < h["response"] < h["resolution"]:
            raise ConfigError(f"tickets.sla_hours.{p}: need 0 < response < resolution")
    for p, r in t["resolution_breach_rate"].items():
        _check_share(f"tickets.resolution_breach_rate.{p}", r)
    _check_share("tickets.kb_link_rate", t["kb_link_rate"])

    if cfg["at_risk_customers"] > v["customers"]:
        raise ConfigError("at_risk_customers cannot exceed customers")

    qi = cfg["quality_issues"]
    for site, s in qi["QI-01_name_variants"].items():
        if site != "variant_weights":
            _check_share(f"QI-01_name_variants.{site}", s)
    _check_sums_to_one("QI-01_name_variants.variant_weights",
                       qi["QI-01_name_variants"]["variant_weights"])
    for site, s in qi["QI-03_mixed_dates"].items():
        _check_share(f"QI-03_mixed_dates.{site}", s)
    _walk_rates("quality_issues", qi)
    if qi["QI-09_tickets_linking_superseded_article"] > v["tickets"]:
        raise ConfigError("QI-09 count cannot exceed tickets")
