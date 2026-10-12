"""Canonical customers and contract lines (ground truth)."""
import datetime as dt
import re
from pathlib import Path

import numpy as np
import pytest

from generator.config import load_config
from generator.customers import (expected_revenue, generate, make_rngs, read_ground_truth_csv,
                                 write_ground_truth)
from generator.names import VARIANT_KINDS, abbreviate, core_name, name_variant

ROOT = Path(__file__).resolve().parents[1]
TREND_START = dt.date(2026, 4, 1)


@pytest.fixture(scope="module")
def cfg():
    return load_config()


@pytest.fixture(scope="module")
def data(cfg):
    rng, fake = make_rngs(cfg["seed"])
    return generate(cfg, rng, fake)


# --- seeded randomness only ---------------------------------------------------

def test_same_seed_gives_identical_bytes(cfg, tmp_path):
    write_ground_truth(cfg, tmp_path / "a")
    write_ground_truth(cfg, tmp_path / "b")
    for f in ("customers.csv", "contract_lines.csv"):
        assert (tmp_path / "a" / f).read_bytes() == (tmp_path / "b" / f).read_bytes()


def test_different_seed_gives_different_output(cfg, tmp_path):
    write_ground_truth(cfg, tmp_path / "a")
    write_ground_truth(cfg, tmp_path / "b", seed=cfg["seed"] + 1)
    for f in ("customers.csv", "contract_lines.csv"):
        assert (tmp_path / "a" / f).read_bytes() != (tmp_path / "b" / f).read_bytes()


def test_no_global_random_state_in_generator():
    banned = [r"^\s*import random\b", r"^\s*from random import", r"np\.random\.seed\(",
              r"np\.random\.(rand|randint|choice|uniform|normal|random|shuffle|permutation)\(",
              r"Faker\.seed\(", r"\bfake\.seed\("]
    for path in (ROOT / "generator").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for pattern in banned:
            assert not re.search(pattern, text, re.MULTILINE), f"{path.name}: {pattern}"


def test_ground_truth_file_is_labelled_synthetic(cfg, tmp_path):
    write_ground_truth(cfg, tmp_path)
    first_line = (tmp_path / "customers.csv").read_text(encoding="utf-8").splitlines()[0]
    assert first_line.startswith("# SYNTHETIC DATA")
    assert len(read_ground_truth_csv(tmp_path / "customers.csv")) == cfg["volumes"]["customers"]


# --- counts and roles -------------------------------------------------------------

def test_counts(cfg, data):
    customers, _ = data
    v = cfg["volumes"]
    assert len(customers) == v["customers"]
    assert customers.account_id.is_unique
    assert customers.company_name.map(core_name).is_unique
    assert customers.is_new_in_window.sum() == v["new_customers_in_window"]
    assert (customers.status == "closed").sum() == v["closed_customers"]
    assert customers.at_risk.sum() == cfg["at_risk_customers"]


def test_new_closed_and_at_risk_are_disjoint(data):
    customers, _ = data
    new, closed, risk = customers.is_new_in_window, customers.status == "closed", customers.at_risk
    assert not (new & closed).any()
    assert not (new & risk).any()
    assert not (closed & risk).any()


def test_new_customers_start_inside_window(cfg, data):
    customers, _ = data
    starts = customers.loc[customers.is_new_in_window, "first_contract_start"]
    assert starts.between(cfg["window"]["start"], cfg["as_of_date"]).all()
    assert (customers.loc[~customers.is_new_in_window, "first_contract_start"] < cfg["window"]["start"]).all()


def test_at_risk_customers_are_established(data):
    customers, lines = data
    risk = customers[customers.at_risk]
    assert (risk.status == "active").all()
    assert (risk.first_contract_start < TREND_START).all()
    for bl_list in risk.business_lines:
        bls = bl_list.split(";")
        assert "BL1" in bls and ("BL2" in bls or "BL5" in bls)


# --- closed customers -------------------------------------------------------------

def test_closed_customers_have_closed_date_in_window(cfg, data):
    customers, _ = data
    closed = customers[customers.status == "closed"]
    assert closed.closed_date.map(lambda d: cfg["window"]["start"] <= d <= cfg["window"]["end"]).all()
    assert customers.loc[customers.status == "active", "closed_date"].isna().all()


def test_closed_customer_lines_end_by_closed_date(data):
    customers, lines = data
    closed = customers[customers.status == "closed"].set_index("account_id").closed_date
    their_lines = lines[lines.account_id.isin(closed.index)]
    assert len(their_lines) > 0
    for row in their_lines.itertuples():
        assert row.end_date is not None and row.end_date <= closed[row.account_id]
        assert row.contract_start < closed[row.account_id]
        assert row.renewal_date is None


# --- contract lines -----------------------------------------------------------------

def test_contract_lines_shape(cfg, data):
    customers, lines = data
    assert lines.line_id.is_unique
    assert set(lines.business_line) <= {"BL1", "BL2", "BL3", "BL4"}       # BL5 has no contract line
    assert (lines.loc[lines.business_line == "BL2", "revenue_source"] == "billing").all()
    assert (lines.loc[lines.business_line != "BL2", "revenue_source"] == "crm_modelled").all()
    assert not lines.duplicated(["account_id", "business_line"]).any()
    for bl, (lo, hi) in cfg["contract_values"]["annual_value_range"].items():
        values = lines.loc[lines.business_line == bl, "annual_value"]
        assert values.between(lo, hi).all() and (values % 100 == 0).all()


def test_lines_agree_with_customer_business_lines(data):
    customers, lines = data
    by_customer = lines.groupby("account_id").business_line.apply(set)
    for row in customers.itertuples():
        expected = {bl for bl in row.business_lines.split(";") if bl != "BL5"}
        assert by_customer.get(row.account_id, set()) == expected
        assert (row.shop_annual_budget > 0) == ("BL5" in row.business_lines.split(";"))


def test_line_dates(cfg, data):
    customers, lines = data
    first = customers.set_index("account_id").first_contract_start
    for row in lines.itertuples():
        assert row.contract_start >= first[row.account_id]
        if row.end_date is None:
            assert row.renewal_date > cfg["as_of_date"]
    earliest = lines.groupby("account_id").contract_start.min()
    assert (earliest == first[earliest.index]).all()


# --- look-alike pairs ---------------------------------------------------------------

def test_lookalike_pairs(cfg, data):
    customers, _ = data
    groups = customers[customers.lookalike_group != ""].groupby("lookalike_group")
    assert groups.ngroups == cfg["customers"]["lookalike_company_pairs"]
    for _, g in groups:
        assert len(g) == 2
        assert g.account_id.nunique() == 2              # never share an ACC- ID
        assert g.company_name.map(core_name).nunique() == 2
        a, b = (core_name(n).split() for n in g.company_name)
        assert a[0] == b[0]                             # same leading place word: confusable


def test_no_blocklisted_names(cfg, data):
    customers, _ = data
    for token in cfg["customers"]["name_blocklist"]:
        assert not customers.company_name.str.lower().str.contains(token.lower(), regex=False).any()


# --- revenue mix ----------------------------------------------------------------------

def test_revenue_mix_near_target_for_project_seed(cfg, data):
    rev = expected_revenue(cfg, *data)
    assert rev.diff_pp.abs().max() <= 1.5


def test_revenue_mix_near_target_for_other_seeds(cfg):
    for seed in range(101, 111):
        rng, fake = make_rngs(seed)
        rev = expected_revenue(cfg, *generate(cfg, rng, fake))
        assert rev.diff_pp.abs().max() <= 3.0, f"seed {seed}"


# --- name variants (QI-01) ------------------------------------------------------------

def test_abbreviation_examples():
    assert abbreviate("Tees Valley Logistics Ltd") == "TV Logistics"
    assert abbreviate("Smith & Jones Accountants LLP") == "SJ Accountants"
    assert abbreviate("Stockton Haulage Ltd") == "Stockton Haul."


@pytest.mark.parametrize("kind", VARIANT_KINDS)
def test_every_variant_differs_from_original(data, kind):
    customers, _ = data
    rng = np.random.default_rng(0)
    for name in customers.company_name:
        assert name_variant(name, kind, rng) != name


def test_variant_rules():
    rng = np.random.default_rng(0)
    name = "Tees Valley Logistics Ltd"
    assert name_variant(name, "lowercase", rng) == "tees valley logistics ltd"
    assert name_variant(name, "drop_suffix", rng) == "Tees Valley Logistics"
    up = name_variant(name, "uppercase_truncate", rng)
    assert up == up.upper() and len(up) <= 20
    t = name_variant(name, "typo", rng)
    assert t.endswith(" Ltd") and abs(len(t) - len(name)) <= 1 and t != name
