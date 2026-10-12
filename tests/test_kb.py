"""Knowledge base: schema, policies vs fact sheet, superseded/near-duplicate setup, links."""
import datetime as dt
import re
from pathlib import Path

import pandas as pd
import pytest

from generator.config import load_config
from generator.customers import generate, make_rngs, read_ground_truth_csv
from generator.helpdesk import build, generate_tickets
from generator.kb import REQUIRED_FIELDS, load_kb, load_meta, word_count
from generator.ticket_text import TOPICS

ROOT = Path(__file__).resolve().parents[1]
FACT_SHEET = (ROOT / "docs" / "01_business_processes.md").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def cfg():
    return load_config()


@pytest.fixture(scope="module")
def docs():
    return load_kb()


@pytest.fixture(scope="module")
def by_id(docs):
    return {d["id"]: d for d in docs}


# --- schema ---------------------------------------------------------------------------------

def test_counts(cfg, docs):
    v = cfg["volumes"]
    assert sum(d["type"] == "article" for d in docs) == v["kb_articles"]
    assert sum(d["type"] == "policy" for d in docs) == v["kb_policies"]
    assert sum(d["status"] == "superseded" for d in docs) == v["kb_superseded"]


def test_schema_and_unique_ids(cfg, docs):
    ids = [d["id"] for d in docs]
    assert len(ids) == len(set(ids))
    lo, hi = cfg["window"]["start"], cfg["window"]["end"]
    for d in docs:
        for f in REQUIRED_FIELDS:
            assert f in d, (d["id"], f)
        assert re.fullmatch(r"KB-\d{4}", d["id"])
        assert d["path"].name.startswith(d["id"] + "-")
        assert d["type"] in ("article", "policy")
        assert d["status"] in ("current", "superseded")
        assert isinstance(d["topic_ids"], list) and set(d["topic_ids"]) <= set(TOPICS)
        assert isinstance(d["created"], dt.date) and isinstance(d["updated"], dt.date)
        assert lo <= d["created"] <= d["updated"] <= hi
        assert d.get("synthetic") is True
        assert "Synthetic document" in d["body"]
        assert (d["superseded_by"] is None) == (d["status"] == "current")


def test_every_topic_has_a_current_article(docs):
    covered = {t for d in docs if d["status"] == "current" and len(d["topic_ids"]) == 1 for t in d["topic_ids"]}
    assert covered == set(TOPICS)


def test_documents_long_enough_for_chunking(docs):
    assert min(word_count(d) for d in docs) >= 200


# --- policies must match the fact sheet -------------------------------------------------------

def test_sla_policy_matches_config_and_fact_sheet(cfg, by_id):
    body = by_id["KB-0045"]["body"]
    for p, h in cfg["tickets"]["sla_hours"].items():
        row = next(line for line in body.splitlines() if line.startswith(f"| {p} |"))
        cells = [c.strip() for c in row.strip("|").split("|")]
        assert int(re.match(r"\d+", cells[2]).group()) == h["response"]
        assert int(re.match(r"\d+", cells[3]).group()) == h["resolution"]


def _numbers(text: str) -> set[str]:
    text = re.sub(r"KB-\d{4}", "", text)                         # article references
    text = re.sub(r"\b(P|BL|QI-|PP-|BQ-|D-)\d+\b", "", text)     # labels
    return {n.lstrip("0") or "0" for n in re.findall(r"\d+", text)}


def test_numbers_in_kb_come_from_the_fact_sheet(docs):
    """No invented numbers: every number in any KB document body appears in the fact sheet."""
    allowed = _numbers(FACT_SHEET)
    for d in docs:
        extra = _numbers(d["body"]) - allowed
        assert not extra, (d["id"], extra)


# --- superseded article and near-duplicates -------------------------------------------------------

def test_superseded_article_conflicts_and_is_dated(cfg, by_id):
    meta = load_meta()
    (s,) = meta["superseded"]
    old, new = by_id[s["old"]], by_id[s["current"]]
    assert old["status"] == "superseded" and old["superseded_by"] == new["id"]
    assert old["superseded_on"] == s["superseded_on"] == new["created"]
    assert cfg["window"]["start"] <= old["superseded_on"] <= cfg["window"]["end"]
    assert old["topic_ids"] == new["topic_ids"]
    # the instructions actually differ: old says email and restart; new says phone, don't restart
    assert "email the service desk" in old["body"].lower() and "switch the router off" in old["body"].lower()
    assert "by phone" in new["body"].lower() and "not to restart the router" in new["body"].lower()


def test_near_duplicate_pairs(by_id):
    meta = load_meta()
    assert len(meta["near_duplicates"]) == 4
    for p in meta["near_duplicates"]:
        a, b = (by_id[x] for x in p["pair"])
        assert a["topic_ids"] == b["topic_ids"] == [p["topic"]]
        assert a["author"] != b["author"]
        assert a["status"] == b["status"] == "current"
    assert sum(not p["agree"] for p in meta["near_duplicates"]) == 1


# --- no personal data ---------------------------------------------------------------------------------

def test_no_personal_data_in_kb(cfg, docs):
    customers, _ = generate(cfg, *make_rngs(cfg["seed"]))
    staff = cfg["tickets"]["agents"] + cfg["customers"]["account_managers"]
    email = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
    phone = re.compile(r"(\+44|\b0)\d[\d ]{8,}")
    for d in docs:
        text = d["body"] + " " + d["author"]
        assert not email.search(text), d["id"]
        assert not phone.search(text), d["id"]
        for name in customers.company_name:
            assert name.lower() not in text.lower(), (d["id"], name)
        for person in staff:
            assert person.split()[-1].lower() not in text.lower(), (d["id"], person)


# --- ticket links -----------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def linked(cfg, tmp_path_factory):
    raw_dir, truth_dir = tmp_path_factory.mktemp("raw"), tmp_path_factory.mktemp("truth")
    build(cfg, raw_dir, truth_dir)
    raw = read_ground_truth_csv(raw_dir / "helpdesk_tickets.csv")
    truth = read_ground_truth_csv(truth_dir / "tickets.csv")
    return raw, truth


def test_links_fit_the_ticket(by_id, linked):
    raw, truth = linked
    df = raw.merge(truth, on="ticket_id", suffixes=("", "_truth"))
    used = df[df.kb_article_id != ""]
    assert (used.kb_article_id == used.kb_article_id_truth).all()      # raw still has every link
    for row in used.itertuples():
        d = by_id[row.kb_article_id]
        assert d["type"] == "article" and row.topic_id in d["topic_ids"]
        assert d["created"] <= dt.date.fromisoformat(row.opened_at[:10])


def test_link_rate(cfg, linked):
    raw, _ = linked
    assert (raw.kb_article_id != "").sum() == round(cfg["tickets"]["kb_link_rate"] * len(raw))


def test_qi09_outdated_links(cfg, by_id, linked):
    raw, truth = linked
    df = raw.merge(truth, on="ticket_id", suffixes=("", "_truth"))
    old = next(d for d in by_id.values() if d["status"] == "superseded")
    outdated = df[df.kb_link_outdated.astype(str) == "True"]
    assert len(outdated) == cfg["quality_issues"]["QI-09_tickets_linking_superseded_article"]
    assert (outdated.kb_article_id == old["id"]).all()
    assert (outdated.opened_at.str[:10] >= old["superseded_on"].isoformat()).all()
    # every other link to the old article was made while it was still current
    others = df[(df.kb_article_id == old["id"]) & (df.kb_link_outdated.astype(str) != "True")]
    assert (others.opened_at.str[:10] < old["superseded_on"].isoformat()).all()


def test_ticket_file_unchanged_apart_from_links(cfg, linked):
    raw, _ = linked
    customers, lines = generate(cfg, *make_rngs(cfg["seed"]))
    before, _ = generate_tickets(cfg, customers, lines)
    for col in ("opened_at", "first_responded_at", "closed_at"):
        before[col] = before[col].map(lambda v: "" if pd.isna(v) else v.strftime("%Y-%m-%d %H:%M"))
    for col in [c for c in raw.columns if c != "kb_article_id"]:
        assert raw[col].astype(str).tolist() == before[col].astype(str).tolist(), col
