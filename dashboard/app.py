"""Northgate operations dashboard (step 4.5, D-032).

Run:  uv run streamlit run dashboard/app.py

Only the pages the demo role may open are listed (config/roles.yaml, step 6.3).
"""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dashboard.common import role_selector  # noqa: E402

st.set_page_config(page_title="Northgate operations (synthetic)", layout="wide")
role = role_selector()                                   # demo role, not authentication (D-044)
titles = {"overview": "Overview", "service": "Service", "customers": "Customers", "data_quality": "Data quality",
          "alerts": "Alerts"}
pages = [st.Page(f"pages/{p}.py", title=titles[p], default=(i == 0)) for i, p in enumerate(role.pages)]
st.navigation(pages).run()
