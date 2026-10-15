"""KPI thresholds (step 4.3, D-030): sourced numbers match the fact sheet; new ones are recorded."""
import re

import yaml

from pipeline.settings import ROOT

T = yaml.safe_load((ROOT / "config" / "kpi_thresholds.yaml").read_text(encoding="utf-8"))
FACT_SHEET = (ROOT / "docs" / "01_business_processes.md").read_text(encoding="utf-8")


def _section(number):
    return re.search(rf"^### {re.escape(number)} .*?(?=^### |^## |\Z)", FACT_SHEET, re.M | re.S).group(0)


def test_bq02_numbers_come_from_the_fact_sheet():
    s19, s13 = _section("1.9"), _section("1.3")
    t = T["bq02_near_breach"]
    assert t["share_of_resolution_target"] == 0.5 and "by 50% of its resolution target" in s19
    assert t["p1_management_escalation_hours"] == 2 and "Any P1 still open 2 hours after it was opened" in s19



def test_sla_targets_and_window_come_from_the_fact_sheet():
    s13, s17 = _section("1.3"), _section("1.7")
    t = T["sla_targets"]
    for p in ("P1", "P2", "P3", "P4"):
        r, h = t["response_hours"][p], t["resolution_hours"][p]
        assert re.search(rf"^\| {p} \|.*\| {r} hours? \| {h} hours \|\s*$", s13, re.M), p
    assert (T["next_90_days"]["start"], T["next_90_days"]["end"]) == ("2026-10-01", "2026-12-29")
    assert "Renewal date from 1 October to 29 December 2026 inclusive" in s17


def test_bq16_window_comes_from_bq16():
    reqs = (ROOT / "docs" / "03_requirements.md").read_text(encoding="utf-8")
    assert T["bq16_onboarding"]["window_days"] == 30 and "raised in the first 30 days" in reqs


def test_approved_values_and_definitions():
    assert T["bq04_unusual_volume"]["min_tickets_per_month"] == 4
    assert (T["bq16_onboarding"]["min_tickets"], T["bq16_onboarding"]["min_p1_p2"]) == (3, 1)
    assert T["bq13_date_tolerance"]["max_days_apart"] == 30
    for key in ("bq02_near_breach", "bq04_unusual_volume", "bq16_onboarding", "bq13_date_tolerance"):
        assert len(T[key]["definition"].split()) > 8, key
