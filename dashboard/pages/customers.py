"""Customers: at-risk list with its signals (BQ-06), review queue, unusual volume, onboarding."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st  # noqa: E402

from dashboard.common import money, page_header, query  # noqa: E402

page_header("Customers")
risk = query("SELECT * FROM v_kpi_bq06_summary").iloc[0]
q = query("SELECT * FROM v_kpi_bq14_review_summary").iloc[0]
c1, c2, c3 = st.columns(3)
c1.metric("Customers flagged at risk", f"{int(risk['customers_at_risk'])} of {int(risk['active_customers'])} active")
c2.metric("Records waiting in the review queue", int(q["records_waiting"]))
c3.metric("Revenue on Unknown customer", money(q["revenue_on_unknown"]))

st.subheader("At-risk customers, in order for retention effort")
st.info("This list is a **prioritisation aid, not a prediction**. A customer is flagged when tickets rose and at least "
        "one spend source fell (last 3 months against the previous 3). The signals behind each flag are shown so an "
        "account manager can judge it; a healthy customer can meet the pattern by chance. Records still in the "
        "review queue are not counted for any customer.")
st.dataframe(query(
    "SELECT retention_rank AS \"Rank\", customer_id AS \"Customer\", company_name AS \"Name\", "
    "signals_fired AS \"Signals\", at_risk_score AS \"Signals fired\", tickets_prev AS \"Tickets Apr-Jun\", "
    "tickets_last AS \"Tickets Jul-Sep\", bl2_ratio AS \"BL2 spend, Jul-Sep vs Apr-Jun\", "
    "shop_ratio AS \"Shop spend, Jul-Sep vs Apr-Jun\", annual_contract_value AS \"Annual contract value (£)\" "
    "FROM v_at_risk WHERE at_risk ORDER BY retention_rank"), hide_index=True, width="stretch",
    column_config={"BL2 spend, Jul-Sep vs Apr-Jun": st.column_config.NumberColumn(format="percent"),
                   "Shop spend, Jul-Sep vs Apr-Jun": st.column_config.NumberColumn(format="percent"),
                   "Annual contract value (£)": st.column_config.NumberColumn(format="£%,.0f")})

st.subheader("Unusual ticket volume, September 2026")
st.caption("4 or more tickets in the month (D-030).")
st.dataframe(query("SELECT customer_id AS \"Customer\", company_name AS \"Name\", tickets AS \"Tickets\" "
                   "FROM v_kpi_bq04_ticket_volume WHERE unusual_volume AND year_month = '2026-09' ORDER BY tickets DESC"),
             hide_index=True, width="stretch")

st.subheader("New customers: onboarding")
st.caption("Off track: 3 or more tickets, or any P1/P2, in the first 30 days (D-030).")
st.dataframe(query("SELECT customer_id AS \"Customer\", company_name AS \"Name\", first_contract_start AS \"First contract\", "
                   "tickets_first_30_days AS \"Tickets, first 30 days\", p1_p2_first_30_days AS \"P1/P2, first 30 days\", "
                   "off_track AS \"Off track\" FROM v_kpi_bq16_onboarding ORDER BY off_track DESC, first_contract_start"),
             hide_index=True, width="stretch")
