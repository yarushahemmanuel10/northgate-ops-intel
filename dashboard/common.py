"""Shared dashboard helpers (step 4.5, D-032).

Rules for every page: open DuckDB read-only; only SELECT from warehouse views (filtering and
ordering is fine, arithmetic is not: every number on screen comes from a view, so the dashboard
and the Phase 5 assistant always agree); show the synthetic-data banner and the as-of date; label
BL1, BL3 and BL4 revenue "modelled"; no ticket text and no personal data.
Roles (step 6.3, D-044): each page checks the demo role can open it, and business names appear only on pages
config/roles.yaml allows for that role. The role is a demo selector, not authentication.
"""
from __future__ import annotations

import os
from pathlib import Path

import duckdb
import pandas as pd
import streamlit as st

from pipeline.roles.config import ALERT_RULES as ALERT_RULES_ALL, Role, get_role, load_roles

ROOT = Path(__file__).resolve().parents[1]
ROLE_KEY = "role"


def db_path() -> Path:
    """The warehouse file: NORTHGATE_DB if set (tests, screenshots), else data/warehouse/northgate.duckdb."""
    return Path(os.environ.get("NORTHGATE_DB", ROOT / "data" / "warehouse" / "northgate.duckdb"))


def alerts_db_path() -> Path:
    """The alert history (pipeline.alerts.run): NORTHGATE_ALERTS_DB if set, else data/alerts/alerts.duckdb."""
    return Path(os.environ.get("NORTHGATE_ALERTS_DB", ROOT / "data" / "alerts" / "alerts.duckdb"))


def query(sql: str, source: str = "warehouse") -> pd.DataFrame:
    """Run one SELECT, read-only, against the warehouse or (source="alerts") the alert history."""
    if not sql.lstrip().upper().startswith("SELECT"):
        raise ValueError("the dashboard only selects from the warehouse")
    con = duckdb.connect(str(alerts_db_path() if source == "alerts" else db_path()), read_only=True)
    try:
        return con.execute(sql).df()
    finally:
        con.close()


def current_role() -> Role:
    """The demo role picked in the sidebar; NORTHGATE_ROLE (tests, screenshots) or the default otherwise."""
    return get_role(st.session_state.get(ROLE_KEY) or os.environ.get("NORTHGATE_ROLE"))


def role_selector() -> Role:
    roles, _ = load_roles()
    if ROLE_KEY not in st.session_state:
        st.session_state[ROLE_KEY] = current_role().key
    with st.sidebar:
        st.selectbox("Demo role — not authentication", list(roles), key=ROLE_KEY,
                     format_func=lambda k: roles[k].label,
                     help="Decides which pages, names and assistant tools are shown (config/roles.yaml, D-044). "
                          "Anyone can pick any role: this is a demonstration, not access control.")
    return current_role()


def page_header(title: str, page: str) -> Role:
    """Banner, title and as-of date; stops the page if the current role may not open it."""
    meta = query("SELECT as_of_date, window_start, window_end, synthetic_notice FROM v_meta").iloc[0]
    st.warning(meta["synthetic_notice"], icon="⚠️")
    st.title(title)
    role = current_role()
    if not role.can_open(page):
        st.error(f"The {role.label} role cannot open this page. Pick another demo role in the sidebar.")
        st.stop()
    st.caption(f"Data as of {pd.Timestamp(meta['as_of_date']):%d %B %Y} · data window "
               f"{pd.Timestamp(meta['window_start']):%d %B %Y} to {pd.Timestamp(meta['window_end']):%d %B %Y}"
               f" · demo role: {role.label}")
    with st.sidebar:
        st.caption(f"Warehouse: {db_path().name} (read-only)")
    return role


def name_column(page: str, column: str = "company_name", label: str = "Name") -> str:
    """The SELECT fragment for a business-name column, or nothing if the role may not see names on this page."""
    return f'{column} AS "{label}", ' if current_role().shows_names(page) else ""


def alert_rule_filter() -> str:
    """A WHERE condition limiting alerts to the role's rules (rule IDs are checked against the rule list)."""
    rules = current_role().alert_rules
    return "rule_id IN (" + ", ".join(f"'{r}'" for r in rules) + ")" if rules else "FALSE"


def money(x: float) -> str:
    return f"£{x:,.0f}"
