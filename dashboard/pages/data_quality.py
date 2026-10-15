"""Data quality (BQ-14): sources reconcile, matching, review queue, revenue on Unknown, findings."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st  # noqa: E402

from dashboard.common import money, page_header, query  # noqa: E402

page_header("Data quality")
q = query("SELECT * FROM v_kpi_bq14_review_summary").iloc[0]
c1, c2, c3, c4 = st.columns(4)
c1.metric("Records on Unknown customer", int(q["records_on_unknown"]))
c2.metric("Revenue on Unknown customer", money(q["revenue_on_unknown"]))
c3.metric("Waiting for a person", int(q["records_waiting"]))
c4.metric("Decided by a person", int(q["records_decided"]))

st.subheader("Do the sources reconcile?")
st.caption("Each validated table against the warehouse: rows differ only by marked duplicates and merged CRM records.")
st.dataframe(query("SELECT source_table AS \"Source table\", validated_rows AS \"Validated\", "
                   "duplicates_marked AS \"Duplicates marked\", merged_into_other_rows AS \"Merged\", "
                   "warehouse_table AS \"Warehouse table\", warehouse_rows AS \"Warehouse rows\", "
                   "reconciles AS \"Reconciles\", rejected_rows_for_system AS \"Rejected at ingestion\" "
                   "FROM v_kpi_bq14_sources ORDER BY source_table"), hide_index=True, width="stretch")

st.subheader("Customer matching")
st.dataframe(query("SELECT source AS \"Source\", match_status AS \"Match\", records AS \"Records\" "
                   "FROM v_kpi_bq14_matching ORDER BY source, match_status"), hide_index=True, width="stretch")

st.subheader("Review queue")
st.caption("Names that could not be matched automatically. Decisions go in config/review_decisions.csv (D-029).")
st.dataframe(query("SELECT source_system AS \"Source\", name_as_written AS \"Name as written\", records AS \"Records\", "
                   "waiting AS \"Waiting\", decided AS \"Decided\" FROM v_kpi_bq14_review_queue "
                   "ORDER BY waiting DESC, records DESC"), hide_index=True, width="stretch")

st.subheader("Revenue on Unknown customer, by line")
st.dataframe(query("SELECT line_label AS \"Business line\", revenue_on_unknown AS \"On Unknown (£)\" "
                   "FROM v_dash_revenue_by_line ORDER BY business_line"), hide_index=True, width="stretch",
             column_config={"On Unknown (£)": st.column_config.NumberColumn(format="£%,.2f")})

st.subheader("CRM and billing disagree (BQ-13)")
st.dataframe(query("SELECT check_result AS \"Check\", customer_id AS \"Customer\", line_id AS \"CRM line\", "
                   "contract_ref AS \"Telecoms contract\", days_difference AS \"Days apart\" "
                   "FROM v_kpi_bq13_crm_telecoms WHERE check_result <> 'matched, dates agree' ORDER BY check_result"),
             hide_index=True, width="stretch")

st.subheader("Validation findings by rule")
st.dataframe(query("SELECT rule_id AS \"Rule\", table_name AS \"Table\", field AS \"Field\", action AS \"Action\", "
                   "findings AS \"Findings\", example_detail AS \"Example\" FROM v_kpi_bq14_findings "
                   "ORDER BY findings DESC"), hide_index=True, width="stretch")
