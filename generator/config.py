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
                "revenue_share_targets", "customers", "contract_values", "tickets", "telecoms",
                "at_risk_customers", "quality_issues"):
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
    _check_share("tickets.working_hours_share", t["working_hours_share"])
    h0, h1 = t["working_hours"]
    if not 0 <= h0 < h1 <= 24:
        raise ConfigError("tickets.working_hours must be 0 <= start < end <= 24")
    if t["customer_rate_shape"] <= 0:
        raise ConfigError("tickets.customer_rate_shape must be positive")
    if len(set(t["agents"])) != len(t["agents"]) or not t["agents"]:
        raise ConfigError("tickets.agents must be a non-empty list of unique names")
    _check_sums_to_one("tickets.category_weights", t["category_weights"])
    r = t["at_risk_rise"]
    if r["ratio"] <= 1 or r["min_last_quarter"] < 1:
        raise ConfigError("tickets.at_risk_rise needs ratio > 1 and min_last_quarter >= 1")
    lo, hi = r["previous_quarter_range"]
    if not 0 <= lo <= hi:
        raise ConfigError("tickets.at_risk_rise.previous_quarter_range must be 0 <= min <= max")

    c = cfg["customers"]
    _check_sums_to_one("customers.sector_weights", c["sector_weights"])
    if len(c["account_managers"]) < 1:
        raise ConfigError("customers.account_managers must not be empty")
    for key in ("established_start_range", "new_start_range", "closed_date_range"):
        lo, hi = c[key]
        if not (isinstance(lo, dt.date) and isinstance(hi, dt.date) and lo < hi):
            raise ConfigError(f"customers.{key} must be two dates in increasing order")
    if c["established_start_range"][1] >= start:
        raise ConfigError("established customers must start before the window")
    for key in ("new_start_range", "closed_date_range"):
        lo, hi = c[key]
        if lo < start or hi > end:
            raise ConfigError(f"customers.{key} must lie inside the window")
    lo, hi = c["additional_line_offset_months"]
    if not 0 <= lo <= hi:
        raise ConfigError("customers.additional_line_offset_months must be 0 <= min <= max")
    if 2 * c["lookalike_company_pairs"] > v["customers"]:
        raise ConfigError("lookalike pairs need two customers each")

    cv = cfg["contract_values"]
    if set(cv["annual_value_range"]) != {"BL1", "BL2", "BL3", "BL4"}:
        raise ConfigError("contract_values.annual_value_range must list exactly BL1-BL4")
    for name, (lo, hi) in [*cv["annual_value_range"].items(),
                           ("shop_annual_budget_range", cv["shop_annual_budget_range"])]:
        if not 0 < lo < hi:
            raise ConfigError(f"contract_values {name}: need 0 < min < max")
    _check_sums_to_one("contract_values.term_years_weights",
                       {str(k): w for k, w in cv["term_years_weights"].items()})

    tel = cfg["telecoms"]
    _check_share("telecoms.call_charge_share", tel["call_charge_share"])
    if tel["call_charge_shape"] <= 0:
        raise ConfigError("telecoms.call_charge_shape must be positive")
    fall = tel["at_risk_fall"]
    if not 0 < fall["max_ratio"] < 1:
        raise ConfigError("telecoms.at_risk_fall.max_ratio must be between 0 and 1")
    lo, hi = fall["call_reduction_range"]
    if not 0 < lo <= hi <= 1:
        raise ConfigError("telecoms.at_risk_fall.call_reduction_range must satisfy 0 < min <= max <= 1")
    if 1 - fall["max_ratio"] >= tel["call_charge_share"]:
        raise ConfigError("telecoms: a fall that large cannot come from call charges alone")

    n_special = v["new_customers_in_window"] + v["closed_customers"] + cfg["at_risk_customers"]
    if n_special > v["customers"]:
        raise ConfigError("new, closed and at-risk customers are disjoint and must fit in customers")

    qi = cfg["quality_issues"]
    for site, s in qi["QI-01_name_variants"].items():
        if isinstance(s, (int, float)):
            _check_share(f"QI-01_name_variants.{site}", s)
    _check_sums_to_one("QI-01_name_variants.variant_weights",
                       qi["QI-01_name_variants"]["variant_weights"])
    for site, s in qi["QI-03_mixed_dates"].items():
        if isinstance(s, (int, float)):
            _check_share(f"QI-03_mixed_dates.{site}", s)
    from generator.ticket_text import CATEGORIES
    if sorted(qi["QI-06_category_variants"]) != CATEGORIES:
        raise ConfigError("QI-06_category_variants must list every ticket category")
    _walk_rates("quality_issues", qi)
    if qi["QI-09_tickets_linking_superseded_article"] > v["tickets"]:
        raise ConfigError("QI-09 count cannot exceed tickets")
