# Power BI model: relationships between the exported tables

> **Fictional company, synthetic data.** Files: `data/warehouse/exports/*.csv`, produced by
> `python -m pipeline.run` (or `python -m pipeline.warehouse.export` after a load). No `.pbix`
> file is kept in the repository; the model is built by hand from this note (D-027).

## Loading the files

Import each CSV with **UTF-8** encoding and **first row as headers**. Dates are `YYYY-MM-DD`,
timestamps `YYYY-MM-DD HH:MM:SS`, the decimal mark is `.` and there are no thousands
separators; an empty field is a missing value. Set the locale for these files to English
(United Kingdom) or English (United States) so `.` is read as the decimal mark. Keep
`revenue` as a decimal number: it is stored at full precision so totals match the
reconciliation (D-026); round only in visuals.

## Star schema

Dimensions: `dim_customer` (key `customer_id`), `dim_date` (key `date`),
`dim_business_line` (key `business_line`). Every relationship below is **many-to-one, single
direction, from the fact or view to the dimension**. Where a table has several dates, one
relationship is active and the others are created inactive and used with `USERELATIONSHIP`
in the measures that need them.

| From (many) | Column | To (one) | Active? | Use |
|---|---|---|---|---|
| `fact_ticket` | `customer_id` | `dim_customer[customer_id]` | Yes | |
| `fact_ticket` | `opened_date` | `dim_date[date]` | Yes | ticket volumes by month (BQ-01, 03, 04) |
| `fact_ticket` | `closed_date` | `dim_date[date]` | No | resolutions by close month |
| `fact_ticket` | `business_line` | `dim_business_line[business_line]` | Yes | always BL1 |
| `fact_order` | `customer_id` | `dim_customer[customer_id]` | Yes | |
| `fact_order` | `order_date` | `dim_date[date]` | Yes | orders and BL5 revenue by month |
| `fact_order` | `promised_date`, `delivered_date` | `dim_date[date]` | No | on-time delivery (BQ-10) |
| `fact_order` | `business_line` | `dim_business_line[business_line]` | Yes | always BL5 |
| `fact_order_line` | `customer_id` | `dim_customer[customer_id]` | Yes | |
| `fact_order_line` | `order_date` | `dim_date[date]` | Yes | |
| `fact_order_line` | `business_line` | `dim_business_line[business_line]` | Yes | |
| `fact_bill` | `customer_id` | `dim_customer[customer_id]` | Yes | |
| `fact_bill` | `bill_month` | `dim_date[date]` | Yes | BL2 revenue by month |
| `fact_bill` | `bill_date` | `dim_date[date]` | No | |
| `fact_bill` | `business_line` | `dim_business_line[business_line]` | Yes | always BL2 |
| `fact_contract_line` | `customer_id` | `dim_customer[customer_id]` | Yes | |
| `fact_contract_line` | `contract_start` | `dim_date[date]` | Yes | |
| `fact_contract_line` | `renewal_date`, `end_date` | `dim_date[date]` | No | renewals in the next 90 days (BQ-05) |
| `fact_contract_line` | `business_line` | `dim_business_line[business_line]` | Yes | |
| `v_revenue` | `customer_id` | `dim_customer[customer_id]` | Yes | **the revenue table** |
| `v_revenue` | `month` | `dim_date[date]` | Yes | |
| `v_revenue` | `business_line` | `dim_business_line[business_line]` | Yes | |
| `v_contract_revenue_month` | `customer_id`, `month`, `business_line` | the three dimensions | Yes | audit of modelled revenue |
| `v_revenue_excluded` | `business_line` | `dim_business_line[business_line]` | Yes | revenue left out for unusable values (empty on this data) |

`v_window_month` is a helper list of the 18 months in the data window; it needs no relationship.

**Do not relate `fact_order_line` to `fact_order`.** Both already reach `dim_customer` and
`dim_date`; a direct link between them would give Power BI two paths to the same dimension
(an ambiguous model). Use `order_id` for drill-through instead.

## Measures to get right

- **Revenue:** `SUM(v_revenue[revenue])`. It already applies `revenue_treatment` (cancelled and
  refunded orders out, credit notes in as negative amounts) and the one-source-per-line rule
  (D-003). Do not add up `fact_order_line[line_total]`, `fact_order[order_total]` or
  `fact_bill[total]` directly for revenue: those include rows that must be excluded.
- **Modelled versus measured:** show `v_revenue[revenue_basis]` (or
  `dim_business_line[modelled]`) next to any revenue figure; BL1, BL3 and BL4 are modelled from
  contract values, BL2 and BL5 are measured.
- **Unknown customer:** `dim_customer[is_unknown] = TRUE` is the row that holds queued and
  unmatched records. Company totals include it; customer-level visuals should show it as its
  own bar rather than hide it. `match_status` on each fact says how its customer link was made.
- **Flags:** columns ending `_flag` say why a value is empty or inconsistent. Filter on them
  on purpose, e.g. resolution SLA should exclude tickets with `closed_at_flag` starting
  `TIME-001`, and response SLA uses only `response_sla_measurable = TRUE`.

## Not in the exports

- **`ticket_text`** (ticket descriptions and resolution notes) is not exported: it holds personal
  data and becomes available only after Phase 6 masking.
- `fact_ticket[agent]` and `dim_customer[account_manager]` are staff names, flagged as personal
  data in `semantic/data_dictionary.yaml`; Phase 6 roles decide who may see them.
