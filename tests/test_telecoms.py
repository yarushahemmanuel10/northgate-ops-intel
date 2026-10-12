"""Telecoms contracts and bills: one-to-one with CRM BL2 lines, billing rules, spend fall."""
import datetime as dt

import pandas as pd
import pytest

from generator.config import load_config
from generator.customers import read_ground_truth_csv
from generator.telecoms import (BILL_COLUMNS, CONTRACT_COLUMNS, at_risk_coverage, billing_style, build,
                                meets_fall, quarter_spend)


@pytest.fixture(scope="module")
def cfg():
    return load_config()


@pytest.fixture(scope="module")
def built(cfg, tmp_path_factory):
    raw_dir, truth_dir = tmp_path_factory.mktemp("raw"), tmp_path_factory.mktemp("truth")
    customers, lines, *_ = build(cfg, raw_dir, truth_dir)
    contracts = read_ground_truth_csv(raw_dir / "telecoms_contracts.csv")
    bills = read_ground_truth_csv(raw_dir / "telecoms_bills.csv")
    truth = read_ground_truth_csv(truth_dir / "telecoms_accounts.csv")
    return customers, lines, contracts, bills, truth, raw_dir


def _d(s):
    return dt.date.fromisoformat(s) if s else None


# --- files ------------------------------------------------------------------------------

def test_columns_and_markers(built):
    *_, raw_dir = built
    for name, cols in (("telecoms_contracts.csv", CONTRACT_COLUMNS), ("telecoms_bills.csv", BILL_COLUMNS)):
        lines = (raw_dir / name).read_text(encoding="utf-8").splitlines()
        assert lines[0].startswith("# SYNTHETIC DATA")
        assert lines[1].split(",") == cols


def test_raw_files_never_contain_account_ids(built):
    *_, raw_dir = built
    for name in ("telecoms_contracts.csv", "telecoms_bills.csv"):
        assert "ACC-" not in (raw_dir / name).read_text(encoding="utf-8")


# --- clean one-to-one with CRM BL2 lines ---------------------------------------------------

def test_one_account_per_bl2_line(built):
    _, lines, contracts, _, truth, _ = built
    bl2 = lines[lines.business_line == "BL2"]
    assert len(contracts) == len(bl2) == len(truth)
    assert contracts.account_no.is_unique and contracts.contract_ref.is_unique
    assert set(truth.line_id) == set(bl2.line_id)
    merged = truth.merge(bl2[["line_id", "account_id"]], on="line_id", suffixes=("", "_crm"))
    assert (merged.account_id == merged.account_id_crm).all()


def test_contract_dates_match_crm(built):
    _, lines, contracts, _, truth, _ = built
    m = contracts.merge(truth, on="account_no").merge(lines, on="line_id")
    for row in m.itertuples():
        assert _d(row.start_date) == row.contract_start
        expected_end = row.end_date_y if row.end_date_y is not None else row.renewal_date
        assert _d(row.end_date_x) == expected_end


def test_names_in_billing_system_style(built):
    customers, _, contracts, _, truth, _ = built
    names = customers.set_index("account_id").company_name
    m = contracts.merge(truth, on="account_no")
    assert (m.account_name == m.account_id.map(names).map(billing_style)).all()
    assert (m.account_name == m.account_name.str.upper()).all() and (m.account_name.str.len() <= 20).all()


# --- billing rules ------------------------------------------------------------------------------

def test_bills_inside_contract_and_window(cfg, built):
    _, lines, contracts, bills, truth, _ = built
    m = bills.merge(contracts, on="account_no").merge(truth, on="account_no").merge(lines, on="line_id")
    month_start = pd.to_datetime(m.bill_month + "-01").dt.date
    assert (month_start >= cfg["window"]["start"]).all() and (month_start <= cfg["window"]["end"]).all()
    start_month = m.contract_start.map(lambda d: d.replace(day=1))
    assert (month_start >= start_month).all()
    ended = m[m.end_date_y.notna()]
    assert (month_start[ended.index] < ended.end_date_y).all()      # nothing on or after closure


def test_bill_dated_month_end_and_september_issued(cfg, built):
    _, lines, contracts, bills, truth, _ = built
    for row in bills.itertuples():
        y, mth = map(int, row.bill_month.split("-"))
        nxt = dt.date(y + (mth == 12), mth % 12 + 1, 1)
        assert _d(row.bill_date) == nxt - dt.timedelta(days=1)
    active = truth.merge(lines, on="line_id")
    active = set(active.loc[active.end_date.isna(), "account_no"])
    sept = set(bills.loc[bills.bill_month == "2026-09", "account_no"])
    assert active <= sept


def test_amounts(built):
    _, _, contracts, bills, _, _ = built
    assert (bills.call_charges >= 0).all()
    assert ((bills.line_rental + bills.call_charges - bills.total).abs() < 0.011).all()
    m = bills.merge(contracts[["account_no", "monthly_charge", "start_date"]], on="account_no")
    assert (m.line_rental <= m.monthly_charge + 1e-9).all()
    # every month after the first and before the last bills exactly the fixed line rental
    first = m.groupby("account_no").bill_month.transform("min")
    last = m.groupby("account_no").bill_month.transform("max")
    middle = m[(m.bill_month != first) & (m.bill_month != last)]
    assert len(middle) > 0
    assert (middle.line_rental == middle.monthly_charge).all()


def test_twelve_months_near_annual_value(built):
    customers, lines, _, bills, truth, _ = built
    risk = set(customers.loc[customers.at_risk, "account_id"])
    year = bills[bills.bill_month.between("2025-04", "2026-03")]
    totals = year.groupby("account_no").total.agg(["sum", "size"])
    m = totals.merge(truth, left_index=True, right_on="account_no").merge(lines, on="line_id")
    full = m[(m["size"] == 12) & ~m.account_id_x.isin(risk)
             & (m.contract_start <= dt.date(2025, 4, 1))]
    assert len(full) >= 20
    assert ((full["sum"] / full.annual_value - 1).abs() <= 0.10).all()


# --- spend fall (BQ-06) ------------------------------------------------------------------------------

def test_at_risk_bl2_customers_meet_fall_rule(cfg, built):
    customers, _, _, bills, truth, _ = built
    flags = meets_fall(quarter_spend(bills, truth), cfg)
    risk_bl2 = [a for a in customers.loc[customers.at_risk, "account_id"] if a in flags.index]
    assert risk_bl2 and flags.loc[risk_bl2].all()


def test_few_active_healthy_customers_meet_fall_rule_by_chance(cfg, built):
    customers, _, _, bills, truth, _ = built
    flags = meets_fall(quarter_spend(bills, truth), cfg)
    healthy = customers.loc[~customers.at_risk & (customers.status == "active"), "account_id"]
    assert flags.reindex(healthy, fill_value=False).sum() <= 2


def test_every_at_risk_customer_has_a_fall_source(built):
    customers = built[0]
    cov = at_risk_coverage(customers)
    assert len(cov) == customers.at_risk.sum()
    assert (cov.fall_sources != "NONE").all()


# --- determinism ---------------------------------------------------------------------------------------

def test_same_seed_identical_bytes(cfg, tmp_path):
    build(cfg, tmp_path / "r1", tmp_path / "t1")
    build(cfg, tmp_path / "r2", tmp_path / "t2")
    for name in ("telecoms_contracts.csv", "telecoms_bills.csv"):
        assert (tmp_path / "r1" / name).read_bytes() == (tmp_path / "r2" / name).read_bytes()
    assert (tmp_path / "t1/telecoms_accounts.csv").read_bytes() == (tmp_path / "t2/telecoms_accounts.csv").read_bytes()


def test_different_seed_different_output(cfg, tmp_path):
    build(cfg, tmp_path / "r1", tmp_path / "t1")
    build(cfg, tmp_path / "r2", tmp_path / "t2", seed=cfg["seed"] + 1)
    assert (tmp_path / "r1/telecoms_bills.csv").read_bytes() != (tmp_path / "r2/telecoms_bills.csv").read_bytes()
