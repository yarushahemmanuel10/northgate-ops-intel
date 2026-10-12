"""Generator config: internal validity and agreement with the fact sheet."""
import copy
import datetime as dt
import re
from pathlib import Path

import pytest

from generator.config import ConfigError, load_config, validate

ROOT = Path(__file__).resolve().parents[1]
FACT_SHEET = (ROOT / "docs" / "01_business_processes.md").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def cfg():
    return load_config()


def _table_rows(text: str, first_cell: str) -> list[list[str]]:
    rows = []
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells and cells[0] == first_cell:
            rows.append(cells)
    return rows


def _section(heading: str) -> str:
    """Text of one fact-sheet subsection, so labels repeated elsewhere don't collide."""
    start = FACT_SHEET.index(heading)
    nxt = FACT_SHEET.find("\n### ", start + len(heading))
    return FACT_SHEET[start: nxt if nxt != -1 else None]


def _number(cell: str) -> int:
    return int(re.search(r"\d[\d,]*", cell).group().replace(",", ""))


# --- agreement with docs/01_business_processes.md ---------------------------

def test_as_of_date_matches_fact_sheet(cfg):
    (row,) = _table_rows(_section("### 1.1"), "As-of date")
    assert dt.datetime.strptime(row[1].split(" — ")[0], "%d %B %Y").date() == cfg["as_of_date"]


def test_window_matches_fact_sheet(cfg):
    (row,) = _table_rows(_section("### 1.1"), "Data window for this prototype")
    start, end = re.match(r"(.+?) to (.+?) \(", row[1]).groups()
    assert dt.datetime.strptime(start, "%d %B %Y").date() == cfg["window"]["start"]
    assert dt.datetime.strptime(end, "%d %B %Y").date() == cfg["window"]["end"]


@pytest.mark.parametrize("priority", ["P1", "P2", "P3", "P4"])
def test_sla_targets_match_fact_sheet(cfg, priority):
    (row,) = _table_rows(_section("### 1.3"), priority)
    assert _number(row[2]) == cfg["tickets"]["sla_hours"][priority]["response"]
    assert _number(row[3]) == cfg["tickets"]["sla_hours"][priority]["resolution"]


@pytest.mark.parametrize("label, key", [
    ("Customers", "customers"),
    ("Support tickets", "tickets"),
    ("Shop orders", "orders"),
    ("New customers onboarded during the window", "new_customers_in_window"),
])
def test_volumes_match_fact_sheet(cfg, label, key):
    (row,) = _table_rows(_section("### 1.6"), label)
    assert _number(row[1]) == cfg["volumes"][key]


def test_kb_total_matches_fact_sheet(cfg):
    (row,) = _table_rows(_section("### 1.6"), "Knowledge base")
    v = cfg["volumes"]
    assert _number(row[1]) == v["kb_articles"] + v["kb_policies"]


@pytest.mark.parametrize("bl", ["BL1", "BL2", "BL3", "BL4", "BL5"])
def test_revenue_shares_match_fact_sheet(cfg, bl):
    (row,) = _table_rows(_section("### 1.2"), bl)
    assert _number(row[-1]) / 100 == pytest.approx(cfg["revenue_share_targets"][bl])


# --- internal validation catches mistakes -----------------------------------

def test_config_is_valid(cfg):
    validate(cfg)


@pytest.mark.parametrize("mutate, message", [
    (lambda c: c["tickets"]["priority_mix"].update(P1=0.5), "sum to 1"),
    (lambda c: c["revenue_share_targets"].update(BL5=0.5), "sum to 1"),
    (lambda c: c.update(as_of_date=dt.date(2026, 10, 12)), "as_of_date"),
    (lambda c: c["quality_issues"]["QI-08_missing_values"].update(crm_renewal_date_rate=1.5), "between 0 and 1"),
    (lambda c: c["volumes"].update(open_tickets_at_as_of=99999), "open tickets"),
    (lambda c: c["tickets"]["sla_hours"]["P1"].update(response=10), "response < resolution"),
])
def test_validation_rejects_bad_values(cfg, mutate, message):
    bad = copy.deepcopy(cfg)
    mutate(bad)
    with pytest.raises(ConfigError, match=message):
        validate(bad)
