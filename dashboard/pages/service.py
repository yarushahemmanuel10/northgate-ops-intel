"""Service: open tickets (BQ-02), SLA compliance (BQ-01) and ticket age."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st  # noqa: E402

from dashboard.common import page_header, query  # noqa: E402

page_header("Service")
s = query("SELECT * FROM v_kpi_bq02_summary").iloc[0]
c1, c2, c3, c4 = st.columns(4)
c1.metric("About to breach (live risk)", int(s["about_to_breach"]))
c2.metric("Breached, still open", int(s["breached"]))
c3.metric("Open tickets", int(s["open_tickets"]))
c4.metric("P1 escalations (open 2h+)", int(s["p1_escalations"]))

cols = ("ticket_id AS \"Ticket\", priority AS \"Priority\", category AS \"Category\", customer_id AS \"Customer\", "
        "opened_at AS \"Opened\", age_hours AS \"Age (hours)\", resolution_target_hours AS \"Target (hours)\", "
        "share_of_target_used AS \"Share of target used\"")
fmt = {"Age (hours)": st.column_config.NumberColumn(format="%.1f"),
       "Share of target used": st.column_config.NumberColumn(format="percent")}
st.subheader("About to breach: live risks")
st.caption("Still open and at least half-way through the resolution target (fact sheet §1.9), not yet past it.")
st.dataframe(query(f"SELECT {cols} FROM v_kpi_bq02_open_tickets WHERE alert = 'about to breach' "
                   "ORDER BY share_of_target_used DESC"), hide_index=True, width="stretch", column_config=fmt)
st.subheader("Breached and still open")
st.caption("Past the full resolution target. Most of this backlog is a side effect of how the synthetic data left "
           "September tickets open (D-030), not a realistic service desk.")
st.dataframe(query(f"SELECT {cols} FROM v_kpi_bq02_open_tickets WHERE alert = 'breached' "
                   "ORDER BY share_of_target_used DESC"), hide_index=True, width="stretch", column_config=fmt)

st.subheader("Age of open tickets")
st.bar_chart(query("SELECT ticket_id AS \"Ticket\", age_hours AS \"Age (hours)\", alert AS \"Alert\" "
                   "FROM v_kpi_bq02_open_tickets ORDER BY age_hours DESC"),
             x="Ticket", y="Age (hours)", color="Alert", x_label="Open ticket")

st.subheader("SLA compliance by priority, whole window")
st.dataframe(query("SELECT priority AS \"Priority\", closed_tickets AS \"Closed\", response_compliance AS \"Response met\", "
                   "response_excluded AS \"Response: excluded\", resolution_compliance AS \"Resolution met\", "
                   "resolution_excluded AS \"Resolution: excluded\" FROM v_kpi_bq01_summary ORDER BY priority"),
             hide_index=True, width="stretch",
             column_config={"Response met": st.column_config.NumberColumn(format="percent"),
                            "Resolution met": st.column_config.NumberColumn(format="percent")})
st.caption("Excluded tickets are counted separately with a reason (no valid priority, no first response, "
           "closed before opened), never dropped. Elapsed hours, not business hours (D-001).")
st.subheader("Resolution SLA compliance by month")
st.caption("A point is shown only when at least 3 tickets of that priority were resolved in the month, so one "
           "breached P1 does not look like a 0% month. The table below gives the counts behind every point.")
st.line_chart(query("SELECT year_month AS \"Month\", priority AS \"Priority\", resolution_compliance AS \"Resolved within target\" "
                    "FROM v_kpi_bq01_sla_monthly WHERE priority <> 'none' AND resolution_measured >= 3 ORDER BY year_month"),
              x="Month", y="Resolved within target", color="Priority", y_label="Share resolved within target")
with st.expander("Tickets behind each month"):
    st.dataframe(query("SELECT year_month AS \"Month\", priority AS \"Priority\", resolution_measured AS \"Resolved, measured\", "
                       "resolution_breached AS \"Breached\", resolution_compliance AS \"Within target\" "
                       "FROM v_kpi_bq01_sla_monthly WHERE priority <> 'none' ORDER BY year_month, priority"),
                 hide_index=True, width="stretch",
                 column_config={"Within target": st.column_config.NumberColumn(format="percent")})

st.subheader("Recent tickets, personal data masked")
st.caption("Ticket text with names, phone numbers, e-mail addresses and business names replaced by placeholders "
           "such as [NAME] and [PHONE] (step 6.1, D-043). The original text never leaves the warehouse.")
st.dataframe(query("SELECT ticket_id AS \"Ticket\", customer_id AS \"Customer\", priority AS \"Priority\", "
                   "category AS \"Category\", opened_at AS \"Opened\", problem_text_masked AS \"Problem (masked)\", "
                   "resolution_text_masked AS \"Resolution (masked)\" FROM v_ticket_masked "
                   "ORDER BY opened_at DESC, ticket_id LIMIT 20"),
             hide_index=True, width="stretch")

