# 01 — Business processes

> **Fictional company, synthetic data.** Northgate IT Services Ltd does not exist. Every
> name, figure and record in this project is invented for a portfolio prototype.

## 1. Company fact sheet

This section fixes the facts that every later document, the data generator and the
evaluation must stay consistent with. If a later step needs a fact that isn't here, it is
added here first.

### 1.1 Company at a glance

| Item | Value |
|---|---|
| Legal name | Northgate IT Services Ltd (fictional) |
| Type | Managed IT services provider (MSP) for small and medium-sized businesses |
| Location | Single office in Stockton-on-Tees; customers across Tees Valley, County Durham and Tyneside |
| Headcount | 25 staff |
| Annual revenue | About £3.2m |
| Customers | About 120 business customers (B2B only; no consumer sales) |
| Typical customer | 10–250 employees; logistics, manufacturing, professional services, education, healthcare (private clinics), retail, charities, construction |
| Data window for this prototype | 1 April 2025 to 30 September 2026 (18 months) |

### 1.2 Business lines

| # | Business line | What it sells | Billing model | Approx. share of revenue |
|---|---|---|---|---|
| BL1 | Managed IT support | Service desk, device management, on-site visits | Monthly contract per user/device | 35% |
| BL2 | Broadband and VoIP telecoms | Business broadband, leased lines, hosted phone systems | Monthly line rental plus call charges | 25% |
| BL3 | Cloud hosting | Hosted servers, backup, Microsoft 365 administration | Monthly per resource | 15% |
| BL4 | Bespoke software | Small line-of-business apps and integrations | Fixed-price projects plus annual support | 10% |
| BL5 | Online IT equipment shop | Laptops, peripherals, networking kit | Per order | 15% |

A customer can buy from several lines. Most take BL1; about half also take BL2; BL5 is
used by most customers occasionally. Revenue shares are targets for the data generator,
not measured figures.

### 1.3 Service levels (BL1 support tickets)

| Priority | Meaning | Response target | Resolution target |
|---|---|---|---|
| P1 | Whole business or critical system down | 1 hour | 4 hours |
| P2 | Major function impaired for many users | 2 hours | 8 hours |
| P3 | Single user or workaround available | 4 hours | 24 hours |
| P4 | Request or question | 8 hours | 72 hours |

Simplification for this prototype: all SLA timers run in elapsed (wall-clock) hours, not
business hours. See DECISIONS.md D-001.

### 1.4 Teams

| Team | Staff | Main system used |
|---|---|---|
| Directors (managing, operations) | 2 | Spreadsheets built from exports |
| Finance | 2 | Telecoms billing system, accounts package (out of scope) |
| Account management | 3 | CRM workbook |
| Service desk (team lead + agents) | 7 | Helpdesk tool, knowledge base |
| Field and infrastructure engineers | 3 | Helpdesk tool |
| Telecoms | 2 | Telecoms billing system |
| Cloud | 2 | Hosting control panels (out of scope) |
| Software development | 3 | Project tracker (out of scope) |
| Procurement and shop | 1 | Online shop admin |
| **Total** | **25** | |

### 1.5 Source systems in scope

| System | Holds | Format the prototype uses |
|---|---|---|
| Helpdesk tool | Support tickets | CSV export |
| CRM workbook | Customer accounts, account managers, contract values, renewal dates | Excel file |
| Online shop | Orders | JSON from a local REST API |
| Telecoms billing system | Telecoms contracts and monthly bills | CSV export |
| Knowledge base | Help articles and SLA policy documents | Markdown files |

Each system uses its own customer identifier and its own way of writing customer names.
There is no shared customer ID. This is the central integration problem the platform
solves. The full inventory, with known quality problems, is in
`02_as_is_architecture.md`.

### 1.6 Volume targets for the generator

| Data | Target volume over 18 months |
|---|---|
| Customers | 120 |
| Support tickets | about 1,500 |
| Shop orders | about 3,000 |
| Knowledge base | 50 documents (help articles plus SLA policies) |
