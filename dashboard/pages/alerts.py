"""Alerts (Layer 4): what the alert runner raised from the KPI views (D-034)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from dashboard.common import alerts_db_path, page_header, query  # noqa: E402

page_header("Alerts")
if not alerts_db_path().exists():
    st.info("No alert run yet. Run `uv run python -m pipeline.alerts.run` (or `python -m pipeline.run`).")
    st.stop()
s = query("SELECT * FROM v_alerts_summary", "alerts").iloc[0]
c1, c2, c3, c4 = st.columns(4)
c1.metric("Active alerts", int(s["active"]))
c2.metric("High (tickets about to breach, P1 open 2h+)", int(s["active_high"]))
c3.metric("Medium (at risk, unusual volume)", int(s["active_medium"]))
c4.metric("Low (CRM and billing disagree)", int(s["active_low"]))
st.caption(f"Last successful run: {pd.Timestamp(s['last_ok_run_at']):%d %B %Y %H:%M} UTC · last run status: "
           f"{s['last_run_status']}. Rules: config/alert_rules.yaml; each reads one KPI view.")

st.subheader("Active alerts")
st.dataframe(query("SELECT severity AS \"Severity\", rule_id AS \"Rule\", bq AS \"Question\", "
                   "customer_id AS \"Customer\", company_name AS \"Name\", reason AS \"Reason\", "
                   "first_raised_at AS \"First raised (UTC)\" FROM v_alerts_active "
                   "ORDER BY severity_rank, rule_id, record_key", "alerts"), hide_index=True, width="stretch")

st.subheader("Alerts by rule")
st.dataframe(query("SELECT rule_id AS \"Rule\", bq AS \"Question\", severity AS \"Severity\", active AS \"Active\", "
                   "cleared AS \"Cleared\" FROM v_alerts_by_rule ORDER BY rule_id", "alerts"),
             hide_index=True, width="stretch")

st.subheader("Recent runs")
st.dataframe(query("SELECT run_id AS \"Run\", started_at AS \"Started (UTC)\", status AS \"Status\", "
                   "alerts_active AS \"Active\", alerts_new AS \"New\", alerts_cleared AS \"Cleared\", "
                   "error AS \"Error\" FROM v_alert_runs ORDER BY run_id DESC LIMIT 10", "alerts"),
             hide_index=True, width="stretch")
