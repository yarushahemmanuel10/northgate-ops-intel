"""Revenue views (step 3.4, D-026), defined exactly as semantic/metrics.yaml revenue_by_line.

- BL1, BL3, BL4 (modelled): CRM contract lines, annual value / 12 for each whole month of the
  data window the line is active. A month counts when the line overlaps any part of it: a
  start, end or closing date in the middle of a month counts that whole month (the same rule
  as the D-007 revenue check).
- BL2 (measured): telecoms bills not marked 'exclude', by bill_month. Credit notes are already
  negative in fact_bill and are added as they are.
- BL5 (measured): shop orders with revenue_treatment 'include', by order month.
Records whose customer is queued or unmatched are on the Unknown customer, so totals are complete.
"""
from __future__ import annotations

import datetime as dt


def create_views(con, cfg: dict) -> None:
    start = dt.date.fromisoformat(cfg["data_window"]["start"]).replace(day=1)
    end = dt.date.fromisoformat(cfg["data_window"]["end"]).replace(day=1)
    con.execute(f"""
        CREATE VIEW v_window_month AS
        SELECT CAST(m AS DATE) AS month
          FROM generate_series(DATE '{start}', DATE '{end}', INTERVAL 1 MONTH) AS t(m)""")
    con.execute("""
        CREATE VIEW v_contract_revenue_month AS
        SELECT c.line_id, c.customer_id, c.business_line, w.month, c.annual_value / 12 AS revenue
          FROM fact_contract_line c
          JOIN dim_business_line b USING (business_line)
          JOIN v_window_month w
            ON c.contract_start <= last_day(w.month) AND (c.end_date IS NULL OR c.end_date >= w.month)
         WHERE b.modelled AND c.annual_value IS NOT NULL AND c.contract_start IS NOT NULL""")
    # Each row is computed exactly and the same on every run: amounts are added as DECIMAL and
    # modelled revenue is divided by 12 once per row. Adding DOUBLEs in parallel would make the
    # last binary digit depend on thread order (found by the rebuild-twice test, D-027).
    con.execute("""
        CREATE VIEW v_revenue AS
        WITH rows AS (
            SELECT c.customer_id, c.business_line, w.month, SUM(c.annual_value) / 12 AS revenue, COUNT(*) AS records
              FROM fact_contract_line c
              JOIN dim_business_line b USING (business_line)
              JOIN v_window_month w
                ON c.contract_start <= last_day(w.month) AND (c.end_date IS NULL OR c.end_date >= w.month)
             WHERE b.modelled AND c.annual_value IS NOT NULL AND c.contract_start IS NOT NULL
             GROUP BY c.customer_id, c.business_line, w.month
            UNION ALL
            SELECT customer_id, business_line, bill_month, CAST(SUM(total) AS DOUBLE), COUNT(*)
              FROM fact_bill
             WHERE revenue_treatment <> 'exclude' AND total IS NOT NULL AND bill_month IS NOT NULL
             GROUP BY customer_id, business_line, bill_month
            UNION ALL
            SELECT customer_id, business_line, CAST(date_trunc('month', order_date) AS DATE),
                   CAST(SUM(order_total) AS DOUBLE), COUNT(*)
              FROM fact_order
             WHERE revenue_treatment = 'include' AND order_total IS NOT NULL AND order_date IS NOT NULL
             GROUP BY customer_id, business_line, CAST(date_trunc('month', order_date) AS DATE))
        SELECT r.customer_id, r.business_line, r.month, strftime(r.month, '%Y-%m') AS year_month,
               r.revenue, CASE WHEN b.modelled THEN 'modelled' ELSE 'measured' END AS revenue_basis, r.records
          FROM rows r JOIN dim_business_line b USING (business_line)""")
    con.execute("""
        CREATE VIEW v_revenue_excluded AS
        SELECT business_line, 'contract line with no usable annual value or start date' AS reason, COUNT(*) AS records
          FROM fact_contract_line c JOIN dim_business_line b USING (business_line)
         WHERE b.modelled AND (c.annual_value IS NULL OR c.contract_start IS NULL) GROUP BY business_line
        UNION ALL
        SELECT business_line, 'bill with no usable total or bill month', COUNT(*)
          FROM fact_bill WHERE revenue_treatment <> 'exclude' AND (total IS NULL OR bill_month IS NULL) GROUP BY business_line
        UNION ALL
        SELECT business_line, 'included order with no usable total or order date', COUNT(*)
          FROM fact_order WHERE revenue_treatment = 'include' AND (order_total IS NULL OR order_date IS NULL) GROUP BY business_line""")


def create_at_risk_view(con, risk: dict, as_of: str) -> None:
    """v_at_risk: one row per canonical customer with the BQ-06 signals (D-028).

    Signals compare the last quarter with the previous one (config/at_risk.yaml):
      ticket_rise  tickets opened: last >= ratio x previous and last >= min_last_quarter
      bl2_fall     BL2 bills not excluded (credit notes included): previous > 0, last/previous <= max
      shop_fall    BL5 orders 'include', all shop accounts combined: previous > 0, last/previous <= max
    at_risk = active customer AND ticket_rise AND (bl2_fall OR shop_fall), as in D-013.
    at_risk_score = number of signals fired (0-3), so the score combines signals and shows them.
    Ranked for retention by at_risk, score, then annual contract value (BQ-06).
    The Unknown customer is left out: its records belong to customers not yet identified.
    """
    q = risk["quarters"]
    p0, p1, l0, l1 = q["previous"]["start"], q["previous"]["end"], q["last"]["start"], q["last"]["end"]
    t, b, s = risk["ticket_rise"], risk["bl2_fall"], risk["shop_fall"]
    con.execute(f"""
        CREATE VIEW v_at_risk AS
        WITH tickets AS (
            SELECT customer_id,
                   COUNT(*) FILTER (WHERE opened_date BETWEEN DATE '{p0}' AND DATE '{p1}') AS tickets_prev,
                   COUNT(*) FILTER (WHERE opened_date BETWEEN DATE '{l0}' AND DATE '{l1}') AS tickets_last
              FROM fact_ticket GROUP BY customer_id),
        bl2 AS (
            SELECT customer_id,
                   COALESCE(SUM(total) FILTER (WHERE bill_month BETWEEN DATE '{p0}' AND DATE '{p1}'), 0) AS bl2_prev,
                   COALESCE(SUM(total) FILTER (WHERE bill_month BETWEEN DATE '{l0}' AND DATE '{l1}'), 0) AS bl2_last
              FROM fact_bill WHERE revenue_treatment <> 'exclude' GROUP BY customer_id),
        shop AS (
            SELECT customer_id,
                   COALESCE(SUM(order_total) FILTER (WHERE order_date BETWEEN DATE '{p0}' AND DATE '{p1}'), 0) AS shop_prev,
                   COALESCE(SUM(order_total) FILTER (WHERE order_date BETWEEN DATE '{l0}' AND DATE '{l1}'), 0) AS shop_last
              FROM fact_order WHERE revenue_treatment = 'include' GROUP BY customer_id),
        contract AS (
            SELECT customer_id, SUM(annual_value) AS annual_contract_value
              FROM fact_contract_line
             WHERE annual_value IS NOT NULL AND (end_date IS NULL OR end_date >= DATE '{as_of}')
             GROUP BY customer_id),
        base AS (
            SELECT d.customer_id, d.company_name, d.status, d.account_manager,
                   COALESCE(t.tickets_prev, 0) AS tickets_prev, COALESCE(t.tickets_last, 0) AS tickets_last,
                   COALESCE(b.bl2_prev, 0) AS bl2_prev, COALESCE(b.bl2_last, 0) AS bl2_last,
                   COALESCE(s.shop_prev, 0) AS shop_prev, COALESCE(s.shop_last, 0) AS shop_last,
                   COALESCE(c.annual_contract_value, 0) AS annual_contract_value
              FROM dim_customer d
              LEFT JOIN tickets t USING (customer_id) LEFT JOIN bl2 b USING (customer_id)
              LEFT JOIN shop s USING (customer_id) LEFT JOIN contract c USING (customer_id)
             WHERE NOT d.is_unknown),
        signals AS (
            SELECT *,
                   CASE WHEN tickets_prev > 0 THEN tickets_last / tickets_prev END AS ticket_ratio,
                   CASE WHEN bl2_prev > 0 THEN CAST(bl2_last AS DOUBLE) / CAST(bl2_prev AS DOUBLE) END AS bl2_ratio,
                   CASE WHEN shop_prev > 0 THEN CAST(shop_last AS DOUBLE) / CAST(shop_prev AS DOUBLE) END AS shop_ratio,
                   (tickets_last >= {t['ratio']} * tickets_prev AND tickets_last >= {t['min_last_quarter']}) AS ticket_rise,
                   (bl2_prev > 0 AND bl2_last <= {b['max_ratio']} * bl2_prev) AS bl2_fall,
                   (shop_prev > 0 AND shop_last <= {s['max_ratio']} * shop_prev) AS shop_fall
              FROM base)
        SELECT customer_id, company_name, status, tickets_prev, tickets_last, ticket_ratio, ticket_rise,
               bl2_prev, bl2_last, bl2_ratio, bl2_fall, shop_prev, shop_last, shop_ratio, shop_fall,
               CAST(ticket_rise AS INTEGER) + CAST(bl2_fall AS INTEGER) + CAST(shop_fall AS INTEGER) AS at_risk_score,
               concat_ws('; ', CASE WHEN ticket_rise THEN 'tickets rising' END,
                               CASE WHEN bl2_fall THEN 'BL2 spend falling' END,
                               CASE WHEN shop_fall THEN 'shop spend falling' END) AS signals_fired,
               (status = 'active' AND ticket_rise AND (bl2_fall OR shop_fall)) AS at_risk,
               annual_contract_value,
               ROW_NUMBER() OVER (ORDER BY (status = 'active' AND ticket_rise AND (bl2_fall OR shop_fall)) DESC,
                                  CAST(ticket_rise AS INTEGER) + CAST(bl2_fall AS INTEGER) + CAST(shop_fall AS INTEGER) DESC,
                                  annual_contract_value DESC, customer_id) AS retention_rank
          FROM signals""")
