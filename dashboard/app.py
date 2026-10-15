"""Northgate operations dashboard (step 4.5, D-032).

Run:  uv run streamlit run dashboard/app.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

st.set_page_config(page_title="Northgate operations (synthetic)", layout="wide")
pages = [st.Page("pages/overview.py", title="Overview", default=True),
         st.Page("pages/service.py", title="Service"),
         st.Page("pages/customers.py", title="Customers"),
         st.Page("pages/data_quality.py", title="Data quality")]
st.navigation(pages).run()
