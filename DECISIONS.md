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

## D-007: Uptake and contract values calibrated to the target revenue mix
- **Choice:** Each business line is taken by exactly `round(rate × 120)` customers (plus a few forced so at-risk customers have the lines their pattern needs). Annual value ranges per line live in `config/generator.yaml` and were calibrated on the average of seeds 1–50, not on the project seed.
- **Expected mix:** computed by `python -m generator.customers` over the 18-month window, counting whole active months. BL2 and BL5 figures at this stage are stand-ins (CRM BL2 value and shop budgets); real BL2 and BL5 revenue is measured after the billing and shop generators run.
- **Alternatives:** independent random uptake per customer (seed-dependent mix: up to 8 points off target on some seeds); tuning ranges on the project seed alone (fits that seed exactly but is overfitted, and drifts on others).
- **Reason:** The mix should be near target because of the design, not because of a lucky seed. Tests check the project seed is within 1.5 points of every target and held-out seeds 101–110 within 3 points.
