"""Shared dashboard helpers (step 4.5, D-032).

Rules for every page: open DuckDB read-only; only SELECT from warehouse views (filtering and
ordering is fine, arithmetic is not: every number on screen comes from a view, so the dashboard
and the Phase 5 assistant always agree); show the synthetic-data banner and the as-of date; label
BL1, BL3 and BL4 revenue "modelled"; no ticket text and no personal data.
"""
from __future__ import annotations

import os
from pathlib import Path

import duckdb
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]


def db_path() -> Path:
    """The warehouse file: NORTHGATE_DB if set (tests, screenshots), else data/warehouse/northgate.duckdb."""
    return Path(os.environ.get("NORTHGATE_DB", ROOT / "data" / "warehouse" / "northgate.duckdb"))


def query(sql: str) -> pd.DataFrame:
    """Run one SELECT against the warehouse, read-only."""
    if not sql.lstrip().upper().startswith("SELECT"):
        raise ValueError("the dashboard only selects from the warehouse")
    con = duckdb.connect(str(db_path()), read_only=True)
    try:
        return con.execute(sql).df()
    finally:
        con.close()


def page_header(title: str) -> None:
    meta = query("SELECT as_of_date, window_start, window_end, synthetic_notice FROM v_meta").iloc[0]
    st.warning(meta["synthetic_notice"], icon="⚠️")
    st.title(title)
    st.caption(f"Data as of {pd.Timestamp(meta['as_of_date']):%d %B %Y} · data window "
               f"{pd.Timestamp(meta['window_start']):%d %B %Y} to {pd.Timestamp(meta['window_end']):%d %B %Y}")
    with st.sidebar:
        st.selectbox("Role", ["All views (roles arrive in Phase 6)"], disabled=True,
                     help="Placeholder: Phase 6 adds roles that decide who sees what.")
        st.caption(f"Warehouse: {db_path().name} (read-only)")


def money(x: float) -> str:
    return f"£{x:,.0f}"
