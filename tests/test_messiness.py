"""Quality problems: complete answer key, independent streams, recoverable dates, no collisions."""
import copy
import datetime as dt
import re

import pandas as pd
import pytest

from generator.config import load_config
from generator.customers import read_ground_truth_csv
from generator.messiness import (Mess, apply_part_a, apply_part_b, build, clean_tables, counts_table, decode,
                                 revenue_check, undo, write_tables)
from generator.patterns import compare


@pytest.fixture(scope="module")
def cfg():
    return load_config()


@pytest.fixture(scope="module")
def built(cfg, tmp_path_factory):
    raw_dir, truth_dir = tmp_path_factory.mktemp("raw"), tmp_path_factory.mktemp("truth")
    clean, mess, known_mem, context, report = build(cfg, raw_dir, truth_dir)
    known = read_ground_truth_csv(truth_dir / "known_issues.csv")
    return clean, mess, known, context, report, raw_dir, truth_dir


# --- the answer key is complete ----------------------------------------------------------------

def test_undo_restores_clean_files_exactly(built, tmp_path):
    clean, mess, known, context, *_ = built
    restored = undo(mess.tables, known)                    # uses only known_issues.csv
    write_tables(clean, context, tmp_path / "clean")
    write_tables(restored, context, tmp_path / "restored")
    for f in sorted((tmp_path / "clean").iterdir()):
        assert f.read_bytes() == (tmp_path / "restored" / f.name).read_bytes(), f.name


def test_messy_files_differ_from_clean(built, tmp_path):
    clean, mess, known, context, *_ = built
    write_tables(clean, context, tmp_path / "clean")
    write_tables(mess.tables, context, tmp_path / "messy")
    for f in sorted((tmp_path / "clean").iterdir()):
        assert f.read_bytes() != (tmp_path / "messy" / f.name).read_bytes(), f.name


def test_counts_match_config(cfg, built):
    clean, _, known, *_ = built
    table = counts_table(cfg, known, clean)
    numeric = table[table.target != "-"]
    for row in numeric.itertuples():
        assert str(row.actual) == str(row.target), row


# --- one stream per quality problem ---------------------------------------------------------------

def test_changing_one_rate_does_not_move_other_problems(cfg):
    other = copy.deepcopy(cfg)
    other["quality_issues"]["QI-06_category_variants_rate"] = 0.10
    def log(c):
        tables, context = clean_tables(c)
        m = Mess(tables)
        apply_part_a(c, m, context)
        apply_part_b(c, m, context)
        return m.known_issues()
    a, b = log(cfg), log(other)
    for q in ("QI-01", "QI-03", "QI-04", "QI-05", "QI-07", "QI-08", "QI-10", "QI-11"):
        cols = ["table", "op", "record_key", "field", "original_json", "new_json"]
        assert a[a.qi == q][cols].reset_index(drop=True).equals(b[b.qi == q][cols].reset_index(drop=True)), q
    assert (a.qi == "QI-06").sum() != (b.qi == "QI-06").sum()


# --- dates can be recovered by rule ------------------------------------------------------------------

def _parse_crm(s):
    for fmt in ("%d/%m/%Y", "%d %b %Y"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ValueError(s)


def _parse_bill_month(s):
    for fmt in ("%b-%y", "%m/%Y"):
        try:
            return dt.datetime.strptime(s, fmt).strftime("%Y-%m")
        except ValueError:
            pass
    raise ValueError(s)


def test_non_iso_dates_parse_back_by_documented_convention(built):
    _, _, known, *_ = built
    q3 = known[known.qi == "QI-03"]
    assert len(q3) > 0
    for e in q3.itertuples():
        new, old = decode(e.new_json), decode(e.original_json)
        if e.table in ("crm_accounts", "crm_lines"):
            assert _parse_crm(new) == old
        elif e.table == "telecoms_bills":
            assert _parse_bill_month(new) == old
        else:
            assert dt.datetime.strptime(new, "%d/%m/%Y %H:%M").strftime("%Y-%m-%d %H:%M") == old


# --- names ----------------------------------------------------------------------------------------------

def test_no_variant_equals_another_customers_name(built):
    _, mess, _, context, *_ = built
    customers = context["customers"]
    real = {n.lower(): a for a, n in zip(customers.account_id, customers.company_name)}
    truth = context["tickets_truth"].set_index("ticket_id").account_id
    for r in mess.tables["helpdesk"].rows:
        owner = real.get(r["customer_name"].lower())
        assert owner in (None, truth[r["ticket_id"]])
    shop = context["shop_truth"].set_index("shop_customer_id").account_id
    for o in mess.tables["shop_orders"].rows:
        owner = real.get(o["company_name"].lower())
        assert owner in (None, shop[o["customer_id"]])


def test_every_second_shop_account_has_a_variant_name(built):
    _, mess, _, context, *_ = built
    st = context["shop_truth"]
    names = context["customers"].set_index("account_id").company_name
    seconds = st[st.account_kind == "second"].set_index("shop_customer_id").account_id
    for o in mess.tables["shop_orders"].rows:
        if o["customer_id"] in seconds.index:
            assert o["company_name"] != names[seconds[o["customer_id"]]]


def test_no_account_ids_outside_the_crm(built):
    *_, raw_dir, _ = built
    for f in ("helpdesk_tickets.csv", "telecoms_contracts.csv", "telecoms_bills.csv", "shop_orders.json"):
        assert "ACC-" not in (raw_dir / f).read_text(encoding="utf-8"), f


# --- QI-04 and QI-05 details ------------------------------------------------------------------------------

def test_crm_duplicates_have_own_ids_and_a_map(cfg, built):
    *_, context, report, raw_dir, truth_dir = built
    acc_map = read_ground_truth_csv(truth_dir / "crm_account_map.csv")
    dups = acc_map[acc_map.raw_account_id != acc_map.account_id]
    assert len(dups) == cfg["quality_issues"]["QI-04_duplicates"]["crm_duplicate_accounts"]
    assert not set(dups.raw_account_id) & set(context["customers"].account_id)
    assert set(dups.account_id) <= set(context["customers"].account_id)
    raw_accounts = pd.read_excel(raw_dir / "crm_accounts.xlsx", sheet_name="Accounts", skiprows=1)
    assert set(dups.raw_account_id) <= set(raw_accounts["Account ID"])


def test_invalid_values_keep_their_originals(built):
    _, _, known, *_ = built
    q5 = known[known.qi == "QI-05"]
    for e in q5.itertuples():
        old = decode(e.original_json)
        if e.field == "priority":
            assert old in ("P1", "P2", "P3", "P4")
        elif e.field == "closed_at":
            assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", old)
        elif e.field == "annual_value":
            assert isinstance(old, int) and old > 0
        elif e.field == "lines":
            assert all(ln["quantity"] >= 1 for ln in old)


# --- determinism ------------------------------------------------------------------------------------------

def test_same_seed_identical_bytes(cfg, tmp_path):
    build(cfg, tmp_path / "r1", tmp_path / "t1")
    build(cfg, tmp_path / "r2", tmp_path / "t2")
    for d1, d2 in (("r1", "r2"), ("t1", "t2")):
        for f in sorted((tmp_path / d1).iterdir()):
            assert f.read_bytes() == (tmp_path / d2 / f.name).read_bytes(), f.name


# --- Part B ----------------------------------------------------------------------------------------------

def test_personal_data_spans_are_recorded_and_fictional(cfg, built):
    clean, mess, known, context, *_ = built
    spans = known[known.op == "pii_span"]
    assert len(spans) > 0 and set(spans.note) == {"pii:name", "pii:email", "pii:phone"}
    messy = {r["ticket_id"]: r["description"] for r in mess.tables["helpdesk"].rows}
    original = {r["ticket_id"]: r["description"] for r in clean["helpdesk"].rows}
    staff = {p.split()[-1].lower() for p in cfg["tickets"]["agents"] + cfg["customers"]["account_managers"]}
    for e in spans.itertuples():
        text = e.new_value
        assert text in messy[e.record_key] and text not in original[e.record_key]
        if e.note == "pii:phone":
            assert re.fullmatch(r"(07700 900|01632 960)\d{3}", text)
        elif e.note == "pii:email":
            assert text.endswith(".example") and "@" in text
        else:
            assert text.split()[-1].lower() not in staff


def test_kb_links_removed_from_raw_only(cfg, built):
    clean, mess, known, context, *_ = built
    truth = context["tickets_truth"].set_index("ticket_id")
    removed = known[(known.qi == "QI-08") & (known.field == "kb_article_id")]
    assert len(removed) == round(0.40 * sum(1 for r in clean["helpdesk"].rows if r["kb_article_id"]))
    for e in removed.itertuples():
        assert truth.at[e.record_key, "kb_article_id"] == decode(e.original_json) != ""


def test_qi11_leaves_planted_customers_alone(cfg, built):
    _, mess, known, context, report, *_ = built
    customers = context["customers"].set_index("account_id")
    protected = set(customers.index[customers.at_risk | (customers.lookalike_group != "")])
    for account_no, acc in report["qi11_removed"]:
        assert acc not in protected
        assert not any(b["account_no"] == account_no for b in mess.tables["telecoms_bills"].rows)
    truth = context["telecoms_truth"].set_index("account_no")
    for account_no, acc in report["qi11_added"]:
        assert truth.at[account_no, "account_id"] == acc and truth.at[account_no, "line_id"] in ("", None)
        assert any(b["account_no"] == account_no for b in mess.tables["telecoms_bills"].rows)
    for _, days in report["qi11_shifted"]:
        assert abs(days) > 30


def test_cancelled_refunded_and_duplicates_excluded_restore_bl5(built):
    clean, mess, known, *_ = built
    rc = revenue_check(clean, mess.tables, known).set_index(["line", "view"]).revenue
    assert abs(rc[("BL5", "messy, correctly cleaned")] - rc[("BL5", "clean (before any mess)")]) < 0.005


def test_planted_pattern_survives_correct_cleaning(cfg, built):
    clean, mess, known, context, *_ = built
    pat = compare(cfg, clean, mess.tables, known, context)
    risk = set(pat["risk"])
    assert set(pat["flagged"]["clean"]) == risk | {"ACC-0076"}
    assert pat["flagged"]["cleaned"] == pat["flagged"]["clean"]
    assert len(risk & set(pat["flagged"]["naive"])) < len(risk)        # uncleaned data misses at-risk customers
