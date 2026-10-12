# Design decisions

One entry per design choice: the choice, the alternatives considered, and the reason.

<!-- Template
## D-NNN: Title
- **Choice:**
- **Alternatives:**
- **Reason:**
-->

## D-001: SLA timers use elapsed hours, not business hours
- **Choice:** Measure SLA response and resolution times in elapsed wall-clock hours.
- **Alternatives:** Business hours only (e.g. 08:00–18:00 Mon–Fri, excluding bank holidays); P1 on 24/7 and P2–P4 on business hours.
- **Reason:** Keeps the KPI logic simple and easy to verify by hand within the time available. Business-hours calendars are a well-understood extension and are listed as future work. Breach rates will look slightly worse than under a business-hours rule.

## D-002: Fixed as-of date of 30 September 2026
- **Choice:** Treat all data as extracted at the end of 30 September 2026 and use that moment for every time-relative calculation (open tickets, ticket age, next 90 days, trend windows). Definitions in `docs/01_business_processes.md` §1.7.
- **Alternatives:** Use the real current date at run time; use the latest timestamp found in the data.
- **Reason:** Results must be identical every time the code runs, including during interviews months later. The real date would make "next 90 days" drift out of the data window; the latest timestamp varies between sources and with injected bad dates.

## D-003: One revenue source per business line
- **Choice:** BL1, BL3, BL4 revenue modelled from CRM contract lines (annual value ÷ 12 per active month); BL2 from telecoms bills only; BL5 from shop orders only. The CRM records value per business line so this split is possible.
- **Alternatives:** Use CRM contract value for all lines; add CRM BL2 value to telecoms bills; leave BL1/BL3/BL4 out of revenue.
- **Reason:** Each pound is counted once. Billing is the record of what was actually charged for telecoms, so the CRM BL2 value is only used for the BQ-13 check. Lines with no transaction source are included but labelled "modelled" rather than presented as measured.

## D-004: Keep response SLA by adding `first_responded_at`
- **Choice:** Add a `first_responded_at` field to the helpdesk export and measure response SLA as well as resolution SLA.
- **Alternatives:** Drop response SLA and report resolution SLA only.
- **Reason:** Response time is a standard MSP service level and appears in the fact sheet's SLA table. Tickets missing the field are excluded from response compliance and counted separately rather than guessed.

## D-005: Python 3.12, uv, exact pins and a committed lock file
- **Choice:** Python 3.12 managed by `uv`. Direct dependencies pinned to exact versions (`==`) in `pyproject.toml`; the full resolved tree, including transitive packages, committed in `uv.lock`. New packages are added per phase, each named in the step that adds it.
- **Alternatives:** pip with `requirements.txt`; lower-bound pins (`>=`); conda; Python 3.11 or 3.13.
- **Reason:** Anyone cloning the repo gets the same versions with `uv sync`, which is needed for every figure to be reproducible. 3.12 meets the brief's 3.11+ requirement and is widely supported by the libraries planned (sentence-transformers, ChromaDB, Prefect, Streamlit). Exact pins in `pyproject.toml` make the versions visible without reading the lock file.
- **Note:** the build environment used during development keeps its virtual environment in `.venv-linux/` (ignored by git), so a local `uv sync` on macOS can create its own `.venv/` without clashing.

## D-006: Fictional company names, with honest wording about real companies
- **Choice:** Customer names are built from generic word lists (North East place words, trade words, legal suffixes, and surnames from a seeded Faker instance). A small blocklist in `config/generator.yaml` rejects names containing specific words (Northgate, real brand names, public-sector words). Every generated file is labelled as synthetic and fictional.
- **What is not claimed:** names were not checked against Companies House or any register of real companies, which cannot be done offline. Because the names are built from common words, some may coincidentally match real businesses. Documents say "fictional" and never "verified not to exist".
- **Alternatives:** an online check against Companies House (needs network and an API key, and still can't rule out unregistered businesses); obviously artificial names such as "Company 017" (removes the realistic name messiness that entity resolution must handle).
- **Reason:** Realistic names are needed for realistic matching problems; honest wording keeps the claim to what can be verified.
- **The provider's own name:** "Northgate IT Services Ltd" is a made-up company name and has no connection to any real company called Northgate, including Northgate plc in Darlington.
- **Name patterns:** place + trade (about 65%), surname + trade (20%), two surnames + trade (10%), place + trade + "Group Ltd" (5%). "& Co" is only used after a surname, and only in sectors where it reads naturally.

## D-007: Uptake and contract values calibrated to the target revenue mix
- **Choice:** Each business line is taken by exactly `round(rate × 120)` customers (plus a few forced so at-risk customers have the lines their pattern needs). Annual value ranges per line live in `config/generator.yaml` and were calibrated on the average of seeds 1–50, not on the project seed (`python -m generator.customers --calibrate 1 50`).
- **Result for the project seed** (`python -m generator.customers`): BL1 +0.7, BL2 −1.7, BL3 +0.1, BL4 −0.1, BL5 +0.9 percentage points from target.
- **Held-out seeds 101–130** (`python -m generator.customers --spread 101 130`), not used for calibration: every line within −2.8 to +2.7 points of target.
- **Tests:** the project seed and held-out seeds 101–110 must each be within ±3 points of every target.
- **How the design got here (exploratory runs of earlier code, not reproducible from the current version):** with independent random uptake per customer and ranges tuned on the project seed alone, the project seed hit every target exactly but seeds 1–20 put BL2 between +2.1 and +13.5 points off. Calibrating on the average of seeds 1–50 but keeping random uptake still left held-out seeds between −7.0 and +5.0 points, and the project seed's BL2 at −6.1. Fixing uptake counts removed most of that variation.
- **Caveat:** BL2 and BL5 figures at this stage are stand-ins (CRM BL2 value and shop budgets). Real BL2 and BL5 revenue is measured after the billing and shop generators run.
- **Reason:** The mix should be near target because of the design, not because of a lucky seed.

## D-008: One independent random stream per concern
- **Choice:** `make_rngs(seed)` creates one numpy Generator and one Faker instance from the project seed. Inside the generator, the numpy Generator is split with `Generator.spawn()` into child streams for names, roles and dates, line uptake and shop budgets, and contract lines. Faker is used only for names.
- **Alternatives:** draw everything from one stream in sequence.
- **Reason:** With one shared stream, any change to how names are drawn shifts every later draw, so a cosmetic change to names would silently change roles, dates and revenue. Separate streams keep each part stable when another part changes (tested in `test_substreams_are_independent`). While building this, a single shared stream also showed that `permutation()` results barely changed when only a few earlier draws changed, which made the coupling between parts hard to reason about. Separate streams avoid relying on that behaviour.

## D-009: Shop-only customers are CRM accounts; "customer since" replaces "first contract start"
- **Choice:** Customers who buy only from the shop (BL5) appear on the CRM Accounts sheet like every other customer, with no rows on the Contract Lines sheet. The Accounts sheet carries `Customer Since`: the date the customer relationship began (for shop-only customers, when their account was opened). Contract dates live only on the Contract Lines sheet. The ground-truth column is renamed from `first_contract_start` to `customer_since` to match.
- **Basis in the fact sheet:** onboarding (`docs/01_business_processes.md` §2.1) starts with the account manager adding every new customer to the CRM workbook, and the CRM is the master list of canonical customers (`02` §1.3). A customer missing from the master list could never be resolved.
- **Consequence:** BQ-16 (onboarding) uses each customer's earliest contract line start, so shop-only customers, who are not onboarded through support, are excluded from it.
- **Alternatives:** leave shop-only customers out of the CRM (breaks the master-list rule and makes their shop orders unmatchable); give them a dummy BL5 contract line (invents a contract that doesn't exist and contradicts D-003).

## D-010: Same synthetic-data marker on every data sheet and CSV
- **Choice:** Row 1 of each data sheet in the CRM workbook holds the same `# SYNTHETIC DATA ...` marker as the first line of the CSV files; headers are on row 2. The workbook also has a README sheet.
- **Alternatives:** README sheet only, with headers on row 1 as in a typical export.
- **Reason:** Every file and every sheet stays labelled even if copied out on its own, and all connectors handle the marker the same way (skip one row). The cost is that the workbook is slightly less like a real export, which is acceptable because realism is not the point of the marker.
- **Determinism:** the workbook's created and modified dates and the zip entry timestamps are fixed at 2026-09-30 18:00, so the same seed always produces a byte-identical file.
