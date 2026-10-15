"""Overview: revenue by line and month, modelled versus measured (BQ-07)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st  # noqa: E402

from dashboard.common import money, page_header, query  # noqa: E402

page_header("Overview")
tot = query("SELECT * FROM v_dash_revenue_total").iloc[0]
c1, c2, c3, c4 = st.columns(4)
c1.metric("Revenue, data window", money(tot["revenue_total"]))
c2.metric("Measured (BL2, BL5)", money(tot["revenue_measured"]))
c3.metric("Modelled (BL1, BL3, BL4)", money(tot["revenue_modelled"]))
c4.metric("On Unknown customer", money(tot["revenue_on_unknown"]))
st.info("BL1, BL3 and BL4 revenue is **modelled** from contract values (annual value ÷ 12 per active month); "
        "BL2 (telecoms bills) and BL5 (shop orders) are **measured**. Amounts exclude VAT.")

st.subheader("Revenue by business line")
by_line = query("SELECT line_label AS \"Business line\", revenue_basis AS \"Basis\", revenue AS \"Revenue (£)\", "
                "revenue_on_unknown AS \"Of which on Unknown customer (£)\" FROM v_dash_revenue_by_line ORDER BY business_line")
st.dataframe(by_line, hide_index=True, width="stretch",
             column_config={"Revenue (£)": st.column_config.NumberColumn(format="£%,.0f"),
                            "Of which on Unknown customer (£)": st.column_config.NumberColumn(format="£%,.0f")})

st.subheader("Revenue by month")
monthly = query("SELECT year_month AS \"Month\", line_label AS \"Business line\", revenue AS \"Revenue\" "
                "FROM v_dash_revenue_monthly ORDER BY year_month, business_line")
st.line_chart(monthly, x="Month", y="Revenue", color="Business line", y_label="Revenue (£, excl. VAT)")
