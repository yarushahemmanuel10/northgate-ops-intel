"""KPI views, one per business question the data allows (step 4.4, D-031).

All KPI logic lives here, in the warehouse, so the dashboard (step 4.5) and the Phase 5 assistant
reuse the same definitions. Definitions follow semantic/metrics.yaml; numbers come from
config/kpi_thresholds.yaml (fact sheet targets and the thresholds approved in D-030).
BQ-06 is v_at_risk (views.py); BQ-07's base is v_revenue. BQ-11 and BQ-15 belong to Phase 5.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from pipeline.settings import ROOT


def load_thresholds(path: Path = ROOT / "config" / "kpi_thresholds.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _case(priority_col: str, hours: dict) -> str:
    return "CASE " + " ".join(f"WHEN {priority_col} = '{p}' THEN {h}" for p, h in hours.items()) + " END"


def _exact_sum(expr: str) -> str:
    """Sum a DOUBLE column exactly: each value is fixed to 10 decimal places, added as DECIMAL, then
    returned as DOUBLE. Adding DOUBLEs in parallel would make the last digit depend on thread order
    (found by the rebuild-twice test, D-027, D-031)."""
    return f"CAST(SUM(CAST({expr} AS DECIMAL(38, 10))) AS DOUBLE)"


def create_kpi_views(con, th: dict, as_of: str) -> None:
    resp = _case("priority", th["sla_targets"]["response_hours"])
    reso = _case("priority", th["sla_targets"]["resolution_hours"])
    asof = f"TIMESTAMP '{as_of} 00:00:00'"
    near = th["bq02_near_breach"]
    vol = th["bq04_unusual_volume"]["min_tickets_per_month"]
    onb = th["bq16_onboarding"]
    n90 = th["next_90_days"]
    tol = th["bq13_date_tolerance"]["max_days_apart"]

    # base: every ticket with its targets, times and SLA outcome (NULL when not measurable)
    con.execute(f"""
        CREATE VIEW v_ticket_sla AS
        SELECT ticket_id, customer_id, priority, category, opened_at, first_responded_at, closed_at,
               strftime(closed_at, '%Y-%m') AS closed_month,
               {resp} AS response_target_hours, {reso} AS resolution_target_hours,
               date_diff('second', opened_at, first_responded_at) / 3600.0 AS response_hours,
               date_diff('second', opened_at, closed_at) / 3600.0 AS resolution_hours,
               CASE WHEN priority IS NULL THEN 'no valid priority'
                    WHEN NOT response_sla_measurable THEN response_sla_flag
                    WHEN first_responded_at_flag IS NOT NULL THEN first_responded_at_flag END AS response_excluded_reason,
               CASE WHEN closed_at IS NULL THEN 'open'
                    WHEN priority IS NULL THEN 'no valid priority'
                    WHEN closed_at_flag IS NOT NULL OR opened_at IS NULL THEN COALESCE(closed_at_flag, opened_at_flag) END
                    AS resolution_excluded_reason
          FROM fact_ticket""")
    con.execute("""
        CREATE VIEW v_kpi_bq01_sla_monthly AS
        SELECT closed_month AS year_month, COALESCE(priority, 'none') AS priority,
               COUNT(*) AS closed_tickets,
               COUNT(*) FILTER (WHERE response_excluded_reason IS NULL) AS response_measured,
               COUNT(*) FILTER (WHERE response_excluded_reason IS NULL AND response_hours <= response_target_hours) AS response_met,
               COUNT(*) FILTER (WHERE response_excluded_reason IS NULL AND response_hours > response_target_hours) AS response_breached,
               COUNT(*) FILTER (WHERE response_excluded_reason IS NOT NULL) AS response_excluded,
               COUNT(*) FILTER (WHERE resolution_excluded_reason IS NULL) AS resolution_measured,
               COUNT(*) FILTER (WHERE resolution_excluded_reason IS NULL AND resolution_hours <= resolution_target_hours) AS resolution_met,
               COUNT(*) FILTER (WHERE resolution_excluded_reason IS NULL AND resolution_hours > resolution_target_hours) AS resolution_breached,
               COUNT(*) FILTER (WHERE resolution_excluded_reason IS NOT NULL) AS resolution_excluded,
               COUNT(*) FILTER (WHERE response_excluded_reason IS NULL AND response_hours <= response_target_hours)
                 / NULLIF(COUNT(*) FILTER (WHERE response_excluded_reason IS NULL), 0) AS response_compliance,
               COUNT(*) FILTER (WHERE resolution_excluded_reason IS NULL AND resolution_hours <= resolution_target_hours)
                 / NULLIF(COUNT(*) FILTER (WHERE resolution_excluded_reason IS NULL), 0) AS resolution_compliance
          FROM v_ticket_sla WHERE closed_at IS NOT NULL
         GROUP BY ALL""")
    con.execute(f"""
        CREATE VIEW v_kpi_bq02_open_tickets AS
        WITH o AS (
            SELECT ticket_id, customer_id, priority, category, opened_at,
                   date_diff('second', opened_at, {asof}) / 3600.0 AS age_hours, {reso} AS resolution_target_hours
              FROM fact_ticket WHERE closed_at IS NULL AND opened_at < {asof})
        SELECT *, age_hours / resolution_target_hours AS share_of_target_used,
               CASE WHEN priority IS NULL THEN 'no valid priority'
                    WHEN age_hours >= resolution_target_hours THEN 'breached'
                    WHEN age_hours >= {near['share_of_resolution_target']} * resolution_target_hours THEN 'about to breach'
                    ELSE 'within target' END AS alert,
               (priority = 'P1' AND age_hours >= {near['p1_management_escalation_hours']}) AS p1_management_escalation
          FROM o""")
    con.execute("""
        CREATE VIEW v_kpi_bq03_resolution_time AS
        SELECT closed_month AS year_month, priority, COALESCE(category, 'none') AS category, COUNT(*) AS tickets,
               SUM(date_diff('second', opened_at, closed_at)) / COUNT(*) / 3600.0 AS mean_hours,
               MEDIAN(resolution_hours) AS median_hours
          FROM v_ticket_sla WHERE resolution_excluded_reason IS NULL
         GROUP BY ALL""")
    con.execute(f"""
        CREATE VIEW v_kpi_bq04_ticket_volume AS
        SELECT t.customer_id, d.company_name, d.is_unknown, strftime(t.opened_date, '%Y-%m') AS year_month,
               COUNT(*) AS tickets,
               CASE WHEN d.is_unknown THEN NULL ELSE COUNT(*) >= {vol} END AS unusual_volume
          FROM fact_ticket t JOIN dim_customer d USING (customer_id)
         WHERE t.opened_date IS NOT NULL
         GROUP BY t.customer_id, d.company_name, d.is_unknown, strftime(t.opened_date, '%Y-%m')""")
    con.execute(f"""
        CREATE VIEW v_kpi_bq05_renewals AS
        SELECT c.line_id, c.customer_id, d.company_name, c.business_line, c.annual_value, c.renewal_date,
               d.account_manager,
               CASE WHEN c.renewal_date IS NULL THEN 'renewal date missing' ELSE 'renews in next 90 days' END AS renewal_status
          FROM fact_contract_line c JOIN dim_customer d USING (customer_id)
         WHERE c.end_date IS NULL
           AND (c.renewal_date BETWEEN DATE '{n90['start']}' AND DATE '{n90['end']}' OR c.renewal_date IS NULL)""")
    con.execute(f"""
        CREATE VIEW v_kpi_bq07_revenue_monthly AS
        SELECT business_line, year_month, revenue_basis,
               {_exact_sum('revenue')} AS revenue,
               {_exact_sum("CASE WHEN customer_id = 'UNKNOWN' THEN revenue ELSE 0 END")} AS revenue_on_unknown
          FROM v_revenue GROUP BY ALL""")
    con.execute(f"""
        CREATE VIEW v_kpi_bq08_customer_summary AS
        WITH r AS (SELECT customer_id,
                          {_exact_sum("CASE WHEN business_line = 'BL1' THEN revenue END")} AS revenue_bl1,
                          {_exact_sum("CASE WHEN business_line = 'BL2' THEN revenue END")} AS revenue_bl2,
                          {_exact_sum("CASE WHEN business_line = 'BL3' THEN revenue END")} AS revenue_bl3,
                          {_exact_sum("CASE WHEN business_line = 'BL4' THEN revenue END")} AS revenue_bl4,
                          {_exact_sum("CASE WHEN business_line = 'BL5' THEN revenue END")} AS revenue_bl5,
                          {_exact_sum('revenue')} AS revenue_total,
                          string_agg(DISTINCT business_line, ', ' ORDER BY business_line) AS lines_with_revenue
                     FROM v_revenue GROUP BY customer_id),
             t AS (SELECT customer_id, COUNT(*) AS tickets,
                          COUNT(*) FILTER (WHERE resolution_excluded_reason IS NULL AND resolution_hours > resolution_target_hours) AS resolution_breaches,
                          COUNT(*) FILTER (WHERE response_excluded_reason IS NULL AND response_hours > response_target_hours) AS response_breaches
                     FROM v_ticket_sla GROUP BY customer_id)
        SELECT d.customer_id, d.company_name, d.status, r.lines_with_revenue,
               COALESCE(r.revenue_bl1, 0) AS revenue_bl1, COALESCE(r.revenue_bl2, 0) AS revenue_bl2,
               COALESCE(r.revenue_bl3, 0) AS revenue_bl3, COALESCE(r.revenue_bl4, 0) AS revenue_bl4,
               COALESCE(r.revenue_bl5, 0) AS revenue_bl5, COALESCE(r.revenue_total, 0) AS revenue_total,
               COALESCE(t.tickets, 0) AS tickets, COALESCE(t.resolution_breaches, 0) AS resolution_breaches,
               COALESCE(t.response_breaches, 0) AS response_breaches
          FROM dim_customer d LEFT JOIN r USING (customer_id) LEFT JOIN t USING (customer_id)""")
    con.execute(f"""
        CREATE VIEW v_kpi_bq09_lines_taken AS
        WITH l AS (
            SELECT customer_id, business_line FROM fact_contract_line
             WHERE end_date IS NULL OR end_date >= DATE '{as_of}'
            UNION
            SELECT customer_id, business_line FROM fact_order WHERE revenue_treatment = 'include')
        SELECT d.customer_id, d.company_name,
               string_agg(l.business_line, ', ' ORDER BY l.business_line) AS lines_taken,
               COUNT(l.business_line) AS line_count, COUNT(l.business_line) = 1 AS single_line
          FROM dim_customer d JOIN l USING (customer_id)
         WHERE d.status = 'active' AND NOT d.is_unknown
         GROUP BY d.customer_id, d.company_name""")
    con.execute("""
        CREATE VIEW v_kpi_bq10_delivery_monthly AS
        SELECT strftime(order_date, '%Y-%m') AS year_month,
               COUNT(*) FILTER (WHERE status = 'delivered' AND delivered_date IS NOT NULL) AS delivered_with_date,
               COUNT(*) FILTER (WHERE status = 'delivered' AND delivered_date <= promised_date) AS on_time,
               COUNT(*) FILTER (WHERE status = 'delivered' AND delivered_date > promised_date) AS late,
               COUNT(*) FILTER (WHERE status = 'delivered' AND delivered_date IS NULL) AS delivered_date_missing,
               COUNT(*) FILTER (WHERE status IN ('placed', 'dispatched')) AS not_yet_delivered,
               COUNT(*) FILTER (WHERE status IN ('cancelled', 'refunded')) AS cancelled_or_refunded,
               COUNT(*) FILTER (WHERE status = 'delivered' AND delivered_date <= promised_date)
                 / NULLIF(COUNT(*) FILTER (WHERE status = 'delivered' AND delivered_date IS NOT NULL), 0) AS on_time_rate
          FROM fact_order WHERE order_date IS NOT NULL GROUP BY ALL""")
    con.execute("""
        CREATE VIEW v_kpi_bq12_kb_usage AS
        SELECT a.kb_id, a.title, a.doc_type, a.status, a.superseded_by, a.superseded_on,
               COUNT(t.ticket_id) AS linked_tickets,
               COUNT(t.ticket_id) FILTER (WHERE a.superseded_on IS NOT NULL AND t.opened_date >= a.superseded_on)
                 AS linked_after_superseded
          FROM dim_kb_article a LEFT JOIN fact_ticket t ON t.kb_article_id = a.kb_id
         GROUP BY ALL""")
    con.execute("""
        CREATE VIEW v_kpi_bq12_link_share AS
        SELECT COUNT(*) AS tickets, COUNT(kb_article_id) AS with_article_link,
               COUNT(*) - COUNT(kb_article_id) AS without_article_link,
               (COUNT(*) - COUNT(kb_article_id)) / COUNT(*) AS share_without_link
          FROM fact_ticket""")
    con.execute(f"""
        CREATE VIEW v_kpi_bq13_crm_telecoms AS
        WITH crm AS (SELECT customer_id, line_id, COALESCE(end_date, renewal_date) AS renewal_date FROM fact_contract_line
                      WHERE business_line = 'BL2'),
             tel AS (SELECT customer_id, contract_ref, account_no, end_date FROM fact_telecoms_contract
                      WHERE customer_id <> 'UNKNOWN')
        SELECT COALESCE(crm.customer_id, tel.customer_id) AS customer_id, crm.line_id, tel.contract_ref, tel.account_no,
               crm.renewal_date AS crm_end_or_renewal_date, tel.end_date AS telecoms_end_date,
               date_diff('day', crm.renewal_date, tel.end_date) AS days_difference,
               CASE WHEN tel.contract_ref IS NULL THEN 'BL2 line in CRM, no telecoms account'
                    WHEN crm.line_id IS NULL THEN 'telecoms account, no BL2 line in CRM'
                    WHEN abs(date_diff('day', crm.renewal_date, tel.end_date)) > {tol} THEN 'dates differ by more than {tol} days'
                    ELSE 'matched, dates agree' END AS check_result
          FROM crm FULL OUTER JOIN tel USING (customer_id)""")
    con.execute("""
        CREATE VIEW v_kpi_bq14_sources AS
        SELECT s.*, (s.validated_rows = s.warehouse_rows + s.duplicates_marked + s.merged_into_other_rows) AS reconciles,
               COALESCE(r.rejected_rows, 0) AS rejected_rows_for_system
          FROM dq_source_count s
          LEFT JOIN (SELECT source_system, COUNT(*) AS rejected_rows FROM dq_rejected_row GROUP BY 1) r USING (source_system)""")
    con.execute("""
        CREATE VIEW v_kpi_bq14_matching AS
        SELECT 'helpdesk' AS source, match_status, COUNT(*) AS records FROM fact_ticket GROUP BY ALL
        UNION ALL SELECT 'shop', match_status, COUNT(*) FROM fact_order GROUP BY ALL
        UNION ALL SELECT 'telecoms bills', match_status, COUNT(*) FROM fact_bill GROUP BY ALL
        UNION ALL SELECT 'telecoms contracts', match_status, COUNT(*) FROM fact_telecoms_contract GROUP BY ALL""")
    con.execute("""
        CREATE VIEW v_kpi_bq14_findings AS
        SELECT rule_id, table_name, field, action, COUNT(*) AS findings, MIN(detail) AS example_detail
          FROM dq_finding GROUP BY ALL""")
    con.execute(f"""
        CREATE VIEW v_kpi_bq16_onboarding AS
        WITH s AS (SELECT customer_id, MIN(contract_start) AS first_contract_start FROM fact_contract_line GROUP BY 1)
        SELECT s.customer_id, d.company_name, s.first_contract_start,
               COUNT(t.ticket_id) AS tickets_first_{onb['window_days']}_days,
               COUNT(t.ticket_id) FILTER (WHERE t.priority IN ('P1', 'P2')) AS p1_p2_first_{onb['window_days']}_days,
               (COUNT(t.ticket_id) >= {onb['min_tickets']}
                OR COUNT(t.ticket_id) FILTER (WHERE t.priority IN ('P1', 'P2')) >= {onb['min_p1_p2']}) AS off_track
          FROM s JOIN dim_customer d USING (customer_id)
          LEFT JOIN fact_ticket t ON t.customer_id = s.customer_id
               AND t.opened_date >= s.first_contract_start
               AND t.opened_date < s.first_contract_start + INTERVAL {onb['window_days']} DAY
         WHERE s.first_contract_start >= (SELECT MIN(month) FROM v_window_month)
           AND s.first_contract_start <= DATE '{as_of}' - INTERVAL {onb['window_days']} DAY
         GROUP BY ALL""")


def create_dashboard_views(con, as_of: str) -> None:
    """Headline figures for the dashboard, so its pages only select (D-032). Each is a total of a KPI
    view above, computed here rather than in the page."""
    es = _exact_sum
    con.execute(f"""
        CREATE VIEW v_meta AS
        SELECT CAST(DATE '{as_of}' - INTERVAL 1 DAY AS DATE) AS as_of_date,
               (SELECT MIN(month) FROM v_window_month) AS window_start,
               last_day((SELECT MAX(month) FROM v_window_month)) AS window_end,
               'Synthetic data: Northgate IT Services Ltd is a fictional company; all names and figures are invented.'
                 AS synthetic_notice""")
    con.execute(f"""
        CREATE VIEW v_dash_revenue_by_line AS
        SELECT r.business_line,
               b.business_line || ' ' || b.name || CASE WHEN b.modelled THEN ' (modelled)' ELSE '' END AS line_label,
               r.revenue_basis, {es('r.revenue')} AS revenue,
               {es("CASE WHEN r.customer_id = 'UNKNOWN' THEN r.revenue ELSE 0 END")} AS revenue_on_unknown
          FROM v_revenue r JOIN dim_business_line b USING (business_line)
         GROUP BY r.business_line, b.business_line, b.name, b.modelled, r.revenue_basis""")
    con.execute(f"""
        CREATE VIEW v_dash_revenue_monthly AS
        SELECT k.business_line, b.business_line || ' ' || b.name || CASE WHEN b.modelled THEN ' (modelled)' ELSE '' END
                 AS line_label, k.year_month, k.revenue_basis, k.revenue, k.revenue_on_unknown
          FROM v_kpi_bq07_revenue_monthly k JOIN dim_business_line b USING (business_line)""")
    con.execute(f"""
        CREATE VIEW v_dash_revenue_total AS
        SELECT {es('revenue')} AS revenue_total,
               {es("CASE WHEN revenue_basis = 'modelled' THEN revenue ELSE 0 END")} AS revenue_modelled,
               {es("CASE WHEN revenue_basis = 'measured' THEN revenue ELSE 0 END")} AS revenue_measured,
               {es("CASE WHEN customer_id = 'UNKNOWN' THEN revenue ELSE 0 END")} AS revenue_on_unknown
          FROM v_revenue""")
    con.execute("""
        CREATE VIEW v_kpi_bq01_summary AS
        SELECT priority, SUM(closed_tickets) AS closed_tickets,
               SUM(response_measured) AS response_measured, SUM(response_breached) AS response_breached,
               SUM(response_excluded) AS response_excluded,
               SUM(response_met) / NULLIF(SUM(response_measured), 0) AS response_compliance,
               SUM(resolution_measured) AS resolution_measured, SUM(resolution_breached) AS resolution_breached,
               SUM(resolution_excluded) AS resolution_excluded,
               SUM(resolution_met) / NULLIF(SUM(resolution_measured), 0) AS resolution_compliance
          FROM v_kpi_bq01_sla_monthly GROUP BY priority""")
    con.execute("""
        CREATE VIEW v_kpi_bq02_summary AS
        SELECT COUNT(*) AS open_tickets,
               COUNT(*) FILTER (WHERE alert = 'breached') AS breached,
               COUNT(*) FILTER (WHERE alert = 'about to breach') AS about_to_breach,
               COUNT(*) FILTER (WHERE alert = 'within target') AS within_target,
               COUNT(*) FILTER (WHERE p1_management_escalation) AS p1_escalations
          FROM v_kpi_bq02_open_tickets""")
    con.execute("""
        CREATE VIEW v_kpi_bq14_review_queue AS
        SELECT source_system, raw_name AS name_as_written,
               COUNT(*) AS records,
               COUNT(*) FILTER (WHERE review_status = 'waiting') AS waiting,
               COUNT(*) FILTER (WHERE review_status = 'decided') AS decided
          FROM dq_review_record GROUP BY source_system, raw_name""")
    con.execute(f"""
        CREATE VIEW v_kpi_bq14_review_summary AS
        SELECT (SELECT COUNT(*) FROM dq_review_record WHERE review_status = 'waiting') AS records_waiting,
               (SELECT COUNT(*) FROM dq_review_record WHERE review_status = 'decided') AS records_decided,
               (SELECT COUNT(DISTINCT source_system || '|' || raw_name) FROM dq_review_record
                 WHERE review_status = 'waiting') AS names_waiting,
               (SELECT COUNT(*) FROM fact_ticket WHERE customer_id = 'UNKNOWN')
             + (SELECT COUNT(*) FROM fact_order WHERE customer_id = 'UNKNOWN')
             + (SELECT COUNT(*) FROM fact_bill WHERE customer_id = 'UNKNOWN') AS records_on_unknown,
               (SELECT {es('revenue')} FROM v_revenue WHERE customer_id = 'UNKNOWN') AS revenue_on_unknown""")
    con.execute("""
        CREATE VIEW v_kpi_bq06_summary AS
        SELECT COUNT(*) FILTER (WHERE at_risk) AS customers_at_risk,
               COUNT(*) FILTER (WHERE status = 'active') AS active_customers
          FROM v_at_risk""")
