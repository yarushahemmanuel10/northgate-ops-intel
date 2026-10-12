"""Helpdesk tickets: ground-truth link, who raises tickets, time rules, SLA, at-risk rise."""
import pandas as pd
import pytest

from generator.config import load_config
from generator.customers import read_ground_truth_csv
from generator.helpdesk import (RAW_COLUMNS, _support_windows, as_of_moment, breach_rates,
                                build, meets_rise, quarter_counts)
from generator.ticket_text import CATEGORIES, TOPICS


@pytest.fixture(scope="module")
def cfg():
    return load_config()


@pytest.fixture(scope="module")
def built(cfg, tmp_path_factory):
    raw_dir, truth_dir = tmp_path_factory.mktemp("raw"), tmp_path_factory.mktemp("truth")
    customers, lines, _, _ = build(cfg, raw_dir, truth_dir)
    raw = read_ground_truth_csv(raw_dir / "helpdesk_tickets.csv")   # read back as written
    truth = read_ground_truth_csv(truth_dir / "tickets.csv")
    return customers, lines, raw, truth, raw_dir, truth_dir


@pytest.fixture(scope="module")
def parsed(built):
    _, _, raw, truth, _, _ = built
    df = raw.copy()
    for col in ("opened_at", "first_responded_at", "closed_at"):
        df[col] = pd.to_datetime(df[col].replace("", None), format="%Y-%m-%d %H:%M")
    return df.merge(truth, on="ticket_id")


# --- files and the ground-truth link ------------------------------------------------

def test_raw_columns_and_marker(built):
    *_, raw_dir, _ = built
    lines = (raw_dir / "helpdesk_tickets.csv").read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("# SYNTHETIC DATA")
    assert lines[1].split(",") == RAW_COLUMNS


def test_raw_file_never_contains_account_ids(built):
    *_, raw_dir, _ = built
    assert "ACC-" not in (raw_dir / "helpdesk_tickets.csv").read_text(encoding="utf-8")


def test_every_ticket_has_a_true_account(built):
    customers, _, raw, truth, _, _ = built
    assert list(truth.columns) == ["ticket_id", "account_id", "topic_id"]
    assert truth.ticket_id.tolist() == raw.ticket_id.tolist()
    assert truth.ticket_id.is_unique
    assert set(truth.account_id) <= set(customers.account_id)
    assert set(truth.topic_id) <= set(TOPICS)


def test_customer_name_matches_true_account(parsed, built):
    customers = built[0]
    names = customers.set_index("account_id").company_name
    assert (parsed.customer_name == parsed.account_id.map(names)).all()   # clean: before mess


# --- who raises tickets, and when ------------------------------------------------------

def test_only_bl1_customers_raise_tickets(parsed, built):
    _, lines, *_ = built
    bl1 = set(lines.loc[lines.business_line == "BL1", "account_id"])
    assert set(parsed.account_id) <= bl1


def test_tickets_inside_support_window(cfg, parsed, built):
    customers, lines, *_ = built
    windows = _support_windows(cfg, customers, lines)
    for row in parsed.itertuples():
        first, last = windows[row.account_id]
        assert first <= row.opened_at.date() <= last


def test_no_tickets_on_or_after_closed_date(parsed, built):
    customers = built[0]
    closed = customers[customers.status == "closed"].set_index("account_id").closed_date
    t = parsed[parsed.account_id.isin(closed.index)]
    assert (t.opened_at.dt.date < t.account_id.map(closed)).all()


# --- time rules -----------------------------------------------------------------------------

def test_open_tickets(cfg, parsed):
    moment = as_of_moment(cfg)
    open_ = parsed[parsed.closed_at.isna()]
    assert len(open_) == cfg["volumes"]["open_tickets_at_as_of"]
    assert (open_.opened_at < moment).all()


def test_closed_ticket_times(cfg, parsed):
    moment = as_of_moment(cfg)
    closed = parsed[parsed.closed_at.notna()]
    assert (closed.closed_at <= moment).all()
    assert (closed.first_responded_at >= closed.opened_at).all()
    assert (closed.first_responded_at <= closed.closed_at).all()
    assert closed.first_responded_at.notna().all()


def test_open_ticket_response_times(cfg, parsed):
    open_ = parsed[parsed.closed_at.isna()]
    responded = open_[open_.first_responded_at.notna()]
    assert (responded.first_responded_at >= responded.opened_at).all()
    assert (responded.first_responded_at <= as_of_moment(cfg)).all()


def test_mostly_working_hours(cfg, parsed):
    h0, h1 = cfg["tickets"]["working_hours"]
    o = parsed.opened_at
    share = ((o.dt.weekday < 5) & (o.dt.hour >= h0) & (o.dt.hour < h1)).mean()
    assert abs(share - cfg["tickets"]["working_hours_share"]) <= 0.03


def test_volume_near_target(cfg, parsed):
    assert abs(len(parsed) - cfg["volumes"]["tickets"]) <= 0.1 * cfg["volumes"]["tickets"]


# --- SLA breaches (elapsed hours, D-001) --------------------------------------------------------

def test_breach_rates_near_target(cfg, built):
    raw = built[2]
    br = breach_rates(cfg, raw)
    assert (abs(br.resolution_breach - br.target_resolution_breach) <= 0.02).all()
    overall = (br.response_breach * br.closed_tickets).sum() / br.closed_tickets.sum()
    assert abs(overall - cfg["tickets"]["response_breach_rate"]) <= 0.01


# --- at-risk rise (BQ-06) -----------------------------------------------------------------------

def test_all_at_risk_customers_meet_the_rise_rule(cfg, built):
    customers, _, raw, truth, _, _ = built
    flags = meets_rise(quarter_counts(raw, truth), cfg)
    risk = customers.loc[customers.at_risk, "account_id"]
    assert flags.reindex(risk, fill_value=False).all()


def test_few_healthy_customers_meet_the_rise_rule_by_chance(cfg, built):
    customers, _, raw, truth, _, _ = built
    flags = meets_rise(quarter_counts(raw, truth), cfg)
    healthy = customers.loc[~customers.at_risk, "account_id"]
    assert flags.reindex(healthy, fill_value=False).sum() <= 2


# --- text, categories and agents ------------------------------------------------------------------

def test_categories_match_config_and_topics(cfg, parsed):
    assert sorted(cfg["tickets"]["category_weights"]) == CATEGORIES
    assert (parsed.category == parsed.topic_id.map(lambda t: TOPICS[t]["category"])).all()
    assert not parsed.description.str.contains("{", regex=False).any()
    assert not parsed.resolution_notes.str.contains("{", regex=False).any()
    assert (parsed.kb_article_id == "").all() or parsed.kb_article_id.isna().all()


def test_priorities_allowed_for_topic(parsed):
    assert all(row.priority in TOPICS[row.topic_id]["priorities"] for row in parsed.itertuples())


def test_agents_never_clash_with_customer_names(cfg, built):
    customers, _, raw, *_ = built
    agents = cfg["tickets"]["agents"]
    assert set(raw.agent) <= set(agents)
    staff_surnames = [a.split()[-1].lower() for a in agents + cfg["customers"]["account_managers"]]
    for name in customers.company_name.str.lower():
        assert not any(s in name for s in staff_surnames), name


# --- determinism ------------------------------------------------------------------------------------

def test_same_seed_identical_bytes(cfg, tmp_path):
    build(cfg, tmp_path / "r1", tmp_path / "t1")
    build(cfg, tmp_path / "r2", tmp_path / "t2")
    assert (tmp_path / "r1/helpdesk_tickets.csv").read_bytes() == (tmp_path / "r2/helpdesk_tickets.csv").read_bytes()
    assert (tmp_path / "t1/tickets.csv").read_bytes() == (tmp_path / "t2/tickets.csv").read_bytes()


def test_different_seed_different_output(cfg, tmp_path):
    build(cfg, tmp_path / "r1", tmp_path / "t1")
    build(cfg, tmp_path / "r2", tmp_path / "t2", seed=cfg["seed"] + 1)
    assert (tmp_path / "r1/helpdesk_tickets.csv").read_bytes() != (tmp_path / "r2/helpdesk_tickets.csv").read_bytes()
