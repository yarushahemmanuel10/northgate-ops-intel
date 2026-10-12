"""Shop orders: time rules, catalogue, accounts, at-risk fall on combined spend, the switch trap."""
import datetime as dt
import json

import pytest

from generator.config import load_config
from generator.customers import read_ground_truth_csv
from generator.shop import ORDERS_FILE, build, meets_shop_fall, orders_frame, quarter_shop_spend


@pytest.fixture(scope="module")
def cfg():
    return load_config()


@pytest.fixture(scope="module")
def built(cfg, tmp_path_factory):
    raw_dir, truth_dir = tmp_path_factory.mktemp("raw"), tmp_path_factory.mktemp("truth")
    customers, lines, payload, truth_mem = build(cfg, raw_dir, truth_dir)
    truth = read_ground_truth_csv(truth_dir / "shop_customers.csv")
    on_disk = json.loads((raw_dir / ORDERS_FILE).read_text(encoding="utf-8"))
    return customers, payload, truth, truth_mem, on_disk, raw_dir


@pytest.fixture(scope="module")
def orders(built):
    customers, payload, truth, *_ = built
    df = orders_frame(payload).merge(truth, left_on="customer_id", right_on="shop_customer_id")
    return df.merge(customers[["account_id", "customer_since", "closed_date", "status", "company_name"]],
                    on="account_id", suffixes=("", "_crm"))


# --- file -----------------------------------------------------------------------------

def test_file_is_marked_synthetic_and_has_no_account_ids(built):
    *_, on_disk, raw_dir = built
    assert on_disk["synthetic"] is True and on_disk["vat"] == "excluded"
    assert "ACC-" not in (raw_dir / ORDERS_FILE).read_text(encoding="utf-8")


def test_volume_near_target(cfg, orders):
    assert abs(len(orders) - cfg["volumes"]["orders"]) <= 0.1 * cfg["volumes"]["orders"]


# --- time rules ------------------------------------------------------------------------------

def test_orders_inside_customer_window(cfg, orders):
    d = orders.order_dt.dt.date
    assert (d >= orders.customer_since).all()
    assert (d >= cfg["window"]["start"]).all() and (d <= cfg["as_of_date"]).all()
    closed = orders[orders.closed_date.notna()]
    assert (closed.order_dt.dt.date < closed.closed_date).all()


def test_delivery_and_promise_dates(cfg, orders):
    d = orders.order_dt.dt.date
    promised = orders.promised_date.map(dt.date.fromisoformat)
    assert (promised >= d).all()
    delivered = orders[orders.delivered_date.notna()]
    dd = delivered.delivered_date.map(dt.date.fromisoformat)
    assert (dd >= delivered.order_dt.dt.date).all() and (dd <= cfg["as_of_date"]).all()
    assert (delivered.status == "delivered").all()
    assert set(orders.loc[orders.delivered_date.isna(), "status"]) <= {"placed", "dispatched"}


def test_orders_are_clean(orders):
    assert set(orders.status) <= {"placed", "dispatched", "delivered"}     # no cancellations yet
    assert orders.order_id.is_unique


# --- products and amounts ----------------------------------------------------------------------

def test_lines_come_from_catalogue(cfg, orders):
    cat = {p["sku"]: p for p in cfg["shop"]["catalogue"]}
    for row in orders.itertuples():
        assert 1 <= len(row.lines) <= cfg["shop"]["lines_per_order"][1]
        for ln in row.lines:
            p = cat[ln["sku"]]
            assert ln["product"] == p["name"] and ln["unit_price"] == p["unit_price"]
            assert 1 <= ln["quantity"] <= p["max_qty"]
            assert abs(ln["line_total"] - ln["unit_price"] * ln["quantity"]) < 0.005
        assert abs(row.order_total - sum(ln["line_total"] for ln in row.lines)) < 0.005


def test_catalogue_names_are_generic(cfg):
    brands = ["dell", "hp ", "lenovo", "apple", "microsoft", "logitech", "cisco", "brother", "samsung", "asus"]
    for p in cfg["shop"]["catalogue"]:
        assert not any(b in p["name"].lower() for b in brands), p["name"]


# --- accounts -----------------------------------------------------------------------------------

def test_shop_accounts(cfg, built, orders):
    customers, _, truth, *_ = built
    bl5 = set(customers.loc[customers.business_lines.str.contains("BL5"), "account_id"])
    assert set(truth.account_id) == bl5
    assert truth.shop_customer_id.is_unique
    assert (truth.account_kind == "second").sum() == cfg["shop"]["second_accounts"]
    assert (orders.company_name == orders.company_name_crm).all()          # clean names for now


# --- at-risk fall and chance counts ----------------------------------------------------------------

def test_all_at_risk_customers_meet_shop_fall_on_combined_spend(cfg, built):
    customers, payload, truth, *_ = built
    flags = meets_shop_fall(quarter_shop_spend(payload, truth, "account_id"), cfg)
    risk = customers.loc[customers.at_risk, "account_id"]
    assert flags.reindex(risk, fill_value=False).all()


def test_an_at_risk_customer_has_two_accounts(built):
    customers, _, truth, *_ = built
    risk = set(customers.loc[customers.at_risk, "account_id"])
    assert set(truth.loc[truth.account_kind == "second", "account_id"]) & risk


def test_account_switch_trap(cfg, built):
    """Per account the switcher looks like a fall; combined it is not (Phase 2 case, D-013)."""
    customers, payload, truth, *_ = built
    switchers = truth.loc[truth.switches_to_second, "account_id"].unique()
    assert len(switchers) == cfg["shop"]["account_switchers"]
    per_acc = meets_shop_fall(quarter_shop_spend(payload, truth, "customer_id"), cfg)
    combined = meets_shop_fall(quarter_shop_spend(payload, truth, "account_id"), cfg)
    status = customers.set_index("account_id")
    for acc in switchers:
        assert status.at[acc, "status"] == "active" and not status.at[acc, "at_risk"]
        primary = truth[(truth.account_id == acc) & (truth.account_kind == "primary")].shop_customer_id.iloc[0]
        assert per_acc[primary]
        assert not combined[acc]


def test_combined_bq06_pattern_rarely_by_chance(cfg, built):
    """Ticket rise AND a spend fall: all at-risk customers, at most 2 healthy ones."""
    from generator.customers import generate, make_rngs
    from generator.helpdesk import generate_tickets, meets_rise, quarter_counts, TIME_FMT
    from generator.telecoms import generate_telecoms, meets_fall, quarter_spend
    customers, payload, truth, *_ = built
    _, lines = generate(cfg, *make_rngs(cfg["seed"]))
    raw_t, truth_t = generate_tickets(cfg, customers, lines)
    raw_t = raw_t.assign(**{c: raw_t[c].map(lambda v: "" if v != v or v is None else v.strftime(TIME_FMT))
                            for c in ("opened_at", "first_responded_at", "closed_at")})
    t = meets_rise(quarter_counts(raw_t, truth_t), cfg)
    _, bills, tel = generate_telecoms(cfg, customers, lines)
    b = meets_fall(quarter_spend(bills, tel), cfg)
    s = meets_shop_fall(quarter_shop_spend(payload, truth, "account_id"), cfg)
    def pattern(a):
        return bool(t.get(a, False)) and (bool(b.get(a, False)) or bool(s.get(a, False)))
    risk = set(customers.loc[customers.at_risk, "account_id"])
    healthy = customers.loc[(customers.status == "active") & ~customers.at_risk, "account_id"]
    assert all(pattern(a) for a in risk)
    assert sum(pattern(a) for a in healthy) <= 2


# --- determinism ------------------------------------------------------------------------------------

def test_same_seed_identical_bytes(cfg, tmp_path):
    build(cfg, tmp_path / "r1", tmp_path / "t1")
    build(cfg, tmp_path / "r2", tmp_path / "t2")
    assert (tmp_path / "r1" / ORDERS_FILE).read_bytes() == (tmp_path / "r2" / ORDERS_FILE).read_bytes()
    assert (tmp_path / "t1/shop_customers.csv").read_bytes() == (tmp_path / "t2/shop_customers.csv").read_bytes()


def test_different_seed_different_output(cfg, tmp_path):
    build(cfg, tmp_path / "r1", tmp_path / "t1")
    build(cfg, tmp_path / "r2", tmp_path / "t2", seed=cfg["seed"] + 1)
    assert (tmp_path / "r1" / ORDERS_FILE).read_bytes() != (tmp_path / "r2" / ORDERS_FILE).read_bytes()
