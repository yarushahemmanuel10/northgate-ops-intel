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
    con.execute("""
        CREATE VIEW v_revenue AS
        WITH rows AS (
            SELECT customer_id, business_line, month, revenue FROM v_contract_revenue_month
            UNION ALL
            SELECT customer_id, business_line, bill_month, CAST(total AS DOUBLE)
              FROM fact_bill
             WHERE revenue_treatment <> 'exclude' AND total IS NOT NULL AND bill_month IS NOT NULL
            UNION ALL
            SELECT customer_id, business_line, CAST(date_trunc('month', order_date) AS DATE), CAST(order_total AS DOUBLE)
              FROM fact_order
             WHERE revenue_treatment = 'include' AND order_total IS NOT NULL AND order_date IS NOT NULL)
        SELECT r.customer_id, r.business_line, r.month, strftime(r.month, '%Y-%m') AS year_month,
               SUM(r.revenue) AS revenue,
               CASE WHEN b.modelled THEN 'modelled' ELSE 'measured' END AS revenue_basis,
               COUNT(*) AS records
          FROM rows r JOIN dim_business_line b USING (business_line)
         GROUP BY r.customer_id, r.business_line, r.month, b.modelled""")
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
