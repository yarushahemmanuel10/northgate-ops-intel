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

## 2. Core processes

Each process below has a map, a short narrative, the systems it touches, and the decisions
managers make in it. The decisions matter most: they are what the platform must support,
and the business questions in `03_requirements.md` are derived from them.

### 2.1 Customer onboarding

```mermaid
flowchart TD
    A[Deal agreed by account manager] --> B[Account created in CRM workbook]
    B --> C{Which business lines?}
    C -->|BL1 support| D[Customer set up in helpdesk tool]
    C -->|BL2 telecoms| E[Customer set up in telecoms billing system]
    C -->|BL5 shop| F[Customer account created in online shop]
    D --> G[Engineer site survey and device audit]
    E --> H[Line order placed with carrier]
    G --> I[Devices enrolled, users added]
    H --> J[Line live, first bill raised]
    F --> K[Customer can order online]
    I --> L[30-day onboarding review]
    J --> L
    K --> L
    L --> M[Customer moves to business as usual]
```

**Narrative.** Once a deal is agreed, the account manager adds the customer to the CRM
workbook with contract value, start date and renewal date. Each other team then creates
the customer again in its own system: the service desk in the helpdesk tool, the telecoms
team in the billing system, procurement in the online shop. Each person types the company
name their own way and each system issues its own ID. Nothing links these records except
the name. After 30 days the account manager runs an onboarding review.

**Systems touched:** CRM workbook, helpdesk tool, telecoms billing system, online shop.

| Decision | Who decides | Data needed | How it is done today |
|---|---|---|---|
| Is onboarding on track, or is the customer already unhappy? | Account manager | Tickets raised in the first 30 days, open P1/P2 tickets | Asks the service desk lead; no report exists |
| Have we started billing for everything sold? | Finance | CRM contract lines compared with telecoms bills and support contract | Manual cross-check of workbook against billing export |
| Which customers took only one line and could buy more? | Account manager, directors | Business lines per customer across all systems | Not possible without matching customers by hand |

**Data created here that causes problems later:** the same customer under different names
and IDs in four systems. This is the root cause of most integration work in Layer 1.

### 2.2 Support ticket lifecycle

```mermaid
flowchart TD
    A[Customer reports issue by phone, email or portal] --> B[Agent logs ticket in helpdesk tool]
    B --> C[Agent sets priority P1-P4 and category]
    C --> D{Known issue?}
    D -->|Yes| E[Agent follows knowledge base article]
    D -->|No| F[Agent investigates]
    E --> G{Resolved at first line?}
    F --> G
    G -->|Yes| H[Resolution notes written]
    G -->|No| I[Escalated to engineer or specialist team]
    I --> J{On-site visit needed?}
    J -->|Yes| K[Engineer visit booked]
    J -->|No| L[Remote fix]
    K --> H
    L --> H
    H --> M[Ticket closed]
    M --> N[Monthly SLA report built by hand]
```

**Narrative.** Customers report problems by phone, email or the portal. An agent logs the
ticket, types the customer name, and sets a priority and category. If the problem is
known, the agent follows a knowledge base article; otherwise they investigate or escalate
to an engineer. SLA clocks run from the moment the ticket opens (see fact sheet §1.3).
When the ticket closes, the agent writes resolution notes and, if relevant, links the
knowledge base article used. Each month the service desk lead exports tickets to a
spreadsheet to calculate SLA compliance.

**Systems touched:** helpdesk tool, knowledge base. Engineers sometimes check the CRM
workbook to see what the customer pays for.

| Decision | Who decides | Data needed | How it is done today |
|---|---|---|---|
| Which open tickets are about to breach SLA, and who should pick them up? | Service desk lead | Open tickets, priority, time elapsed against target | Scans the helpdesk queue by eye |
| Are we meeting SLA by priority this month? | Service desk lead, operations director | Closed tickets with open and close times, priority | Monthly export to spreadsheet, formulas by hand |
| Which customers generate unusual ticket volume, and why? | Service desk lead, account manager | Tickets per customer over time, categories | Rarely done; customer names in tickets are inconsistent |
| Is the knowledge base helping, and which articles are out of date? | Service desk lead | Article links on tickets, first-line resolution rate, article status | Not measured |
| How should a new agent handle this problem? | Agent | Relevant knowledge base articles and similar past tickets | Searches the knowledge base by keyword, or asks a colleague |

**Data quality problems created here:** customer names typed freely by agents, vague
descriptions, inconsistent categories, personal data (names, emails, phone numbers) in
free text, and missing or wrong links to knowledge base articles.
