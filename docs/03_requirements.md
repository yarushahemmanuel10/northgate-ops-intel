# 03 — Requirements: business questions

> **Fictional company, synthetic data.**

These are the questions the platform must answer for Northgate's staff. Each is derived
from a manager decision in `01_business_processes.md` and relieves a pain point in
`02_as_is_architecture.md`. Every later component — KPI, alert, LLM tool, evaluation
question — must trace back to at least one question here. Anything that doesn't is out
of scope.

## 1. Business questions

### BQ-01 — Are we meeting SLA targets by priority, month by month?
- **Asked by:** service desk lead, operations director
- **Decision supported:** `01` §2.2 — are we meeting SLA by priority
- **Sources:** helpdesk
- **Answered by:** Layer 3
- **Relieves:** PP-02, PP-08
- **Answered when:** response SLA compliance (from `first_responded_at`) and resolution
  SLA compliance (from `closed_at`), in % per priority per month, using the targets in
  `01` §1.3 (response SLA kept per D-004), match a hand calculation on a small fixed dataset. Tickets missing
  `first_responded_at` are excluded from response compliance and counted separately.

### BQ-02 — Which open tickets are close to breaching their SLA?
- **Asked by:** service desk lead
- **Decision supported:** `01` §2.2 — which tickets are about to breach
- **Sources:** helpdesk
- **Answered by:** Layer 4
- **Relieves:** PP-03
- **Answered when:** an automated flow writes an alert for every ticket open at the as-of
  moment (`01` §1.7) whose age is past a set share of its resolution target, and for none
  below it, on a fixed test dataset.

### BQ-03 — How long do tickets take to resolve, and is that improving?
- **Asked by:** service desk lead, operations director
- **Decision supported:** `01` §2.2 — are we meeting SLA
- **Sources:** helpdesk
- **Answered by:** Layer 3
- **Relieves:** PP-02
- **Answered when:** mean and median time to resolve per priority and category, per month,
  match a hand calculation; open tickets are excluded and the exclusion is stated.

### BQ-04 — Which customers are raising unusually many tickets?
- **Asked by:** service desk lead, account managers
- **Decision supported:** `01` §2.2 — unusual ticket volume; §2.1 — is onboarding on track
- **Sources:** helpdesk, CRM (to attribute tickets to canonical customers)
- **Answered by:** Layers 1 and 3
- **Relieves:** PP-01
- **Answered when:** ticket volume per canonical customer per month is shown, with
  customers above a stated threshold flagged; tickets that could not be matched to a
  customer are counted separately, not dropped.

### BQ-05 — Which contracts renew in the next 90 days, and what are they worth?
- **Asked by:** account managers, directors
- **Decision supported:** `01` §2.4 — renewals in 90 days
- **Sources:** CRM contract lines (telecoms end dates are compared in BQ-13, not added
  here, so no contract appears twice)
- **Answered by:** Layers 3 and 4
- **Relieves:** PP-04
- **Answered when:** a list of contract lines with renewal dates from 1 October to
  29 December 2026 (`01` §1.7), with annual value, business line and account manager,
  matches a filter applied by hand; contract lines with a missing renewal date are listed
  separately.

### BQ-06 — Which customers are at risk: rising tickets and falling spend?
- **Asked by:** account managers, operations director
- **Decision supported:** `01` §2.4 — is this customer at risk; where should retention
  effort go
- **Sources:** helpdesk, shop, telecoms billing, CRM
- **Answered by:** Layers 1, 2, 3, 4 and 5
- **Relieves:** PP-01
- **Answered when:** each active customer has an at-risk score built from stated, documented
  rules (or a model with an honest evaluation), comparing the last 3 months with the
  previous 3 months (`01` §1.7); the score's inputs can be shown for any customer; and
  customers are ranked for retention effort by score, then annual contract value, with the
  ranking matching a hand-ordered fixed dataset. The score combines signals: shop spend
  alone is too lumpy to flag customers on its own (see DECISIONS.md D-013).

### BQ-07 — What is our revenue by customer and business line, per month?
- **Asked by:** directors, finance
- **Decision supported:** `01` §2.3 — customer spend trend; directors' monthly pack
- **Sources:** one per line, as set in `01` §1.2: CRM contract lines for BL1, BL3 and BL4
  (modelled); telecoms bills only for BL2; shop orders only for BL5
- **Answered by:** Layers 2 and 3
- **Relieves:** PP-02, PP-05
- **Answered when:** revenue excludes cancelled and refunded orders and duplicate bills,
  handles credit notes explicitly, never adds CRM BL2 values to billed BL2 revenue,
  labels BL1/BL3/BL4 figures as modelled, and reconciles with cleaned source totals.

### BQ-08 — What does this customer buy from us, and how are they doing overall?
- **Asked by:** account managers
- **Decision supported:** `01` §2.4 — what to offer at renewal; §2.1 — upsell
- **Sources:** all four data systems
- **Answered by:** Layers 1, 2 and 5
- **Relieves:** PP-01
- **Answered when:** for any canonical customer, the platform returns business lines taken,
  spend by line, ticket count and SLA breaches over a period, with each figure traceable
  to the underlying records.

### BQ-09 — Which customers take only one business line?
- **Asked by:** account managers, directors
- **Decision supported:** `01` §2.1 — which customers could buy more
- **Sources:** all four data systems
- **Answered by:** Layers 2 and 3
- **Relieves:** PP-01
- **Answered when:** a list of active customers with the lines they take matches the
  generator's ground truth for that customer set.

### BQ-10 — Are shop orders delivered on time?
- **Asked by:** procurement, operations director
- **Decision supported:** `01` §2.3 — are orders delivered on time
- **Sources:** shop
- **Answered by:** Layer 3
- **Relieves:** PP-02
- **Answered when:** on-time delivery rate per month is shown, with orders missing a
  delivery date counted separately and not assumed on time or late.

### BQ-11 — How should an agent resolve this problem?
- **Asked by:** service desk agents
- **Decision supported:** `01` §2.2 — how should an agent handle this problem
- **Sources:** knowledge base, past tickets (masked)
- **Answered by:** Layer 5
- **Relieves:** PP-06, PP-07
- **Answered when:** the assistant answers from current articles and similar past tickets,
  cites the ids it used, never cites a superseded article, and abstains when retrieval is
  weak. Measured by the evaluation set in Phase 5.
- **Status (2026-10-18): partly met.** Knowledge-base and data questions are met: the frozen
  assistant passed 26/28 tuning and 13/14 held-out questions, cites the ids it used, never cited
  KB-0026 and declines when evidence is weak (D-041, D-042). Similar-past-ticket search is
  **deferred**: ticket text is masked from Phase 6 (D-043) and goes to the warehouse, exports and
  dashboard only. Adding ticket search would change the frozen assistant after its held-out
  evaluation, leaving no unseen questions to test it, so it is recorded as future work.

### BQ-12 — Is the knowledge base being used, and which articles need attention?
- **Asked by:** service desk lead
- **Decision supported:** `01` §2.2 — is the knowledge base helping
- **Sources:** helpdesk (article links), knowledge base (status)
- **Answered by:** Layer 3
- **Relieves:** PP-06
- **Answered when:** article usage counts and the tickets still linking to superseded
  articles are listed; the share of tickets with no article link is shown.

### BQ-13 — Do the CRM and the telecoms billing system agree?
- **Asked by:** finance, account managers
- **Decision supported:** `01` §2.1 — are we billing for everything sold
- **Sources:** CRM, telecoms contracts
- **Answered by:** Layers 1 and 2
- **Relieves:** PP-05
- **Answered when:** three lists are produced and match the generator's ground truth:
  (a) matched customers whose CRM BL2 renewal date and telecoms end date differ by more
  than a stated tolerance; (b) customers with a BL2 contract line in the CRM but no
  matching telecoms account (line sold, possibly not billed); (c) telecoms accounts with
  no BL2 contract line in the CRM.

### BQ-14 — How trustworthy is today's data?
- **Asked by:** operations director, platform owner
- **Decision supported:** whether figures in any report can be relied on
- **Sources:** all
- **Answered by:** Layers 1, 2 and 4
- **Relieves:** PP-02, PP-08
- **Answered when:** for each source, the platform reports rows read, rows rejected (with
  reasons), customers matched, customers sent to review, and whether reconciliation passed;
  the weekly refresh halts with a clear error if reconciliation fails.

### BQ-15 — What happened in operations this week?
- **Asked by:** directors
- **Decision supported:** directors' weekly review (replaces the manual monthly pack)
- **Sources:** KPI outputs from Layer 3
- **Answered by:** Layer 5
- **Relieves:** PP-02
- **Answered when:** an LLM-written summary is produced from KPI values, and every number
  in its text is checked in code against those values; any mismatch is flagged.

### BQ-16 — Is onboarding on track for new customers?
- **Asked by:** account managers
- **Decision supported:** `01` §2.1 — is onboarding on track
- **Sources:** CRM contract lines (earliest contract start per customer), helpdesk
- **Answered by:** Layers 1 and 3
- **Relieves:** PP-01
- **Answered when:** for every customer whose earliest contract line starts inside the data window
  and at least 30 days before the as-of date, the platform shows tickets and P1/P2
  tickets raised in the first 30 days, flags customers above a stated threshold, and
  matches a hand calculation on a fixed dataset. Shop-only customers have no contract
  line, are not onboarded through support, and are excluded (D-009).

## 2. Out of scope for the prototype

| Question | Why it is out of scope |
|---|---|
| Which products cause the most faults? (`01` §2.3) | Needs links between order lines and tickets, which the source systems don't hold |
| Profitability by customer or line | Needs cost data from the accounts package, which is not a source in this prototype |
| Engineer utilisation and scheduling | Not a decision raised in the four core processes |

## 3. Traceability matrix

Filled in as later phases are built. A blank cell after its phase is finished is a gap.

| BQ | Layer(s) | KPI (Phase 4) | Alert or flow (Phase 5) | LLM tool or capability (Phase 5) | Eval question ids (Phase 5) |
|---|---|---|---|---|---|
| BQ-01 | 3 | | | | |
| BQ-02 | 4 | | | | |
| BQ-03 | 3 | | | | |
| BQ-04 | 1, 3 | | | | |
| BQ-05 | 3, 4 | | | | |
| BQ-06 | 1–5 | | | | |
| BQ-07 | 2, 3 | | | | |
| BQ-08 | 1, 2, 5 | | | | |
| BQ-09 | 2, 3 | | | | |
| BQ-10 | 3 | | | | |
| BQ-11 | 5 | | | `search_knowledge_base` (D-035, D-040) + 13 data tools (D-036); past-ticket search deferred (D-043) | T01–T20, DT01–DT08 (tuning); H01–H10, DH01–DH04 (held-out) — **partly met** |
| BQ-12 | 3 | | | | |
| BQ-13 | 1, 2 | | | | |
| BQ-14 | 1, 2, 4 | | | | |
| BQ-15 | 5 | | | | |
| BQ-16 | 1, 3 | | | | |
