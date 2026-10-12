# 02 — As-is architecture

> **Fictional company, synthetic data.** All systems, identifiers and records describe the
> invented Northgate IT Services Ltd.

This document records how Northgate's data is held and moved today, before the platform
exists. Section 1 is the inventory of source systems. Section 2 (added in step 0.6) shows
how data flows between them and where the pain points are.

## 1. Source system inventory

### 1.1 Summary

| System | Owner | Holds | Format used in prototype | Customer identifier | How data gets out today | Refresh |
|---|---|---|---|---|---|---|
| Helpdesk tool | Service desk lead | Support tickets | CSV export | None — customer name typed by agent | Manual CSV export | Monthly, for SLA report |
| CRM workbook | Account management | Customer accounts, contracts, renewal dates | Excel (.xlsx) | `ACC-0001` style | The workbook itself, emailed around | Edited ad hoc |
| Online shop | Procurement | Orders and order lines | JSON from REST API | Numeric shop customer ID, e.g. `1042` | Admin screen; API exists but unused | Live |
| Telecoms billing system | Telecoms team, Finance | Telecoms contracts, monthly bills | CSV export | Account number, e.g. `TEL-50017` | Manual CSV export | Monthly, after bill run |
| Knowledge base | Service desk lead | Help articles, SLA policies | Markdown with front matter | Not applicable | Read in a browser | Edited ad hoc |

There is no shared customer key. The only thing that links a customer across systems is
the company name, which each team writes differently.

### 1.2 Helpdesk tool

- **Purpose:** log and track support tickets for BL1 managed IT support.
- **Record:** one row per ticket.
- **Key fields:** `ticket_id` (e.g. `INC-2025-00001`), `opened_at`, `closed_at`,
  `priority` (P1–P4), `category`, `customer_name` (free text), `description`,
  `resolution_notes`, `kb_article_id` (optional), `agent`.
- **Identifier scheme:** tickets have IDs; customers do not. Agents type the customer name
  each time.
- **Known quality problems:**
  - Customer names typed inconsistently: abbreviations, lower case, missing "Ltd", typos.
  - Descriptions that are vague ("not working", "issue with PC") or contain typos.
  - Personal data in free text: contact names, email addresses, phone numbers.
  - Categories used inconsistently ("Email", "email", "E-mail", "Outlook").
  - Open tickets have empty `closed_at`; a few closed tickets have `closed_at` earlier
    than `opened_at`.
  - `kb_article_id` often blank even when an article was used; sometimes points to a
    superseded article.

### 1.3 CRM workbook

- **Purpose:** the account managers' list of customers and contracts.
- **Record:** one row per customer account.
- **Key fields:** `account_id` (`ACC-0001`), `company_name`, `sector`,
  `account_manager`, `business_lines`, `contract_value_annual`, `contract_start`,
  `renewal_date`, `status` (active, closed).
- **Identifier scheme:** sequential account IDs; the most trusted customer list, so the
  platform treats it as the master list for canonical customers.
- **Known quality problems:**
  - Dates in mixed formats in the same column (`03/04/2025`, `2025-04-03`,
    `3 Apr 2025`), including ambiguous day/month order.
  - Contract values typed inconsistently (`£12,000`, `12000`, `12k`).
  - Some renewal dates missing.
  - A few duplicate rows for the same customer, created when someone re-added an account.
  - Some customers listed as active who have no activity in any other system.

### 1.4 Online shop

- **Purpose:** B2B equipment orders (BL5).
- **Record:** one order per JSON object, with nested order lines.
- **Key fields:** `order_id` (e.g. `SO-100001`), `customer_id` (numeric shop ID),
  `company_name` (as entered at shop sign-up), `order_date`, `status` (placed,
  dispatched, delivered, cancelled, refunded), `promised_date`, `delivered_date`,
  `lines` (sku, product, quantity, unit_price), `order_total`.
- **Identifier scheme:** the shop issues its own numeric customer IDs. A customer can
  have more than one shop account (for example, separate accounts for two offices).
- **Access:** paginated REST API with a date filter. The platform must call the API, not
  read a file.
- **Known quality problems:**
  - Shop company names differ from CRM names.
  - Duplicate shop accounts for one customer.
  - Duplicate orders from double submission (same customer, same lines, minutes apart).
  - Missing `delivered_date` on delivered orders.
  - Cancelled and refunded orders that inflate revenue unless filtered.
  - Occasional negative or zero quantities.

### 1.5 Telecoms billing system

- **Purpose:** contracts and monthly bills for BL2 broadband and VoIP.
- **Records:** two exports: contracts (one row per service contract) and bills (one row
  per account per month).
- **Key fields — contracts:** `contract_ref`, `account_no` (`TEL-50017`),
  `account_name`, `service_type`, `monthly_charge`, `start_date`, `end_date`.
- **Key fields — bills:** `bill_id`, `account_no`, `bill_month`, `line_rental`,
  `call_charges`, `total`.
- **Identifier scheme:** the billing system's own account numbers; names written in the
  billing system's style (often upper case, abbreviated to fit a field length).
- **Known quality problems:**
  - Account names in upper case or truncated.
  - Contract end dates that disagree with CRM renewal dates.
  - Bill months in mixed formats (`2025-04`, `Apr-25`, `04/2025`).
  - Occasional duplicate bill rows from a re-run bill export.
  - A few negative totals (credit notes) that need to be kept but flagged.

### 1.6 Knowledge base

- **Purpose:** help articles for service desk agents and the SLA policy documents.
- **Record:** one Markdown file per document, with YAML front matter.
- **Key fields (front matter):** `id` (e.g. `KB-0012`, `POL-SLA-01`), `title`,
  `category`, `status` (current, superseded), `superseded_by` (optional), `updated`.
- **Known quality problems:**
  - Near-duplicate articles written by different people for the same problem.
  - At least one superseded article that is still easy to find and still linked from
    tickets.
  - Inconsistent categories compared with the helpdesk tool.

### 1.7 Quality problems the synthetic data must contain

Each problem above becomes a deliberate defect in the generated data (Phase 1, step 1.9),
recorded with ground truth so the platform's handling of it can be measured.

| ID | Problem | Systems | How Layer 1 is expected to handle it |
|---|---|---|---|
| QI-01 | Same customer under different names | All four data systems | Entity resolution with review queue |
| QI-02 | No shared customer ID | All four data systems | Entity resolution; CRM as master list |
| QI-03 | Mixed date formats | CRM, telecoms, helpdesk | Parse to ISO dates; reject ambiguous or unparseable values with a reason |
| QI-04 | Duplicate records | CRM, shop orders, telecoms bills | Deduplicate with a logged rule |
| QI-05 | Invalid values | All | Validation rules; rejected-rows log |
| QI-06 | Inconsistent categories | Helpdesk, knowledge base | Controlled vocabulary mapping |
| QI-07 | Personal data in free text | Helpdesk | Masking before any LLM call (Phase 6) |
| QI-08 | Missing values (renewal dates, delivery dates, KB links) | CRM, shop, helpdesk | Kept and flagged, not invented |
| QI-09 | Superseded knowledge base article | Knowledge base | Excluded from retrieval |
| QI-10 | Cancelled, refunded and credit-note amounts | Shop, telecoms | Kept and flagged; excluded from revenue KPIs |
