# Handover: running, changing and extending Northgate Ops Intel

For whoever picks this up next. All data is synthetic (Northgate IT Services Ltd is fictional). Every choice
referred to here is explained in [`DECISIONS.md`](../DECISIONS.md) under its D-number.

## 1. What is where

| Folder | What it holds |
|---|---|
| `generator/` | Builds the synthetic sources, the planted quality problems and the answer key. Only `eval/` and `tests/` may read its output in `data/clean/` and `data/ground_truth/` (D-015 to D-017) |
| `shop_api/` | Mock shop API (FastAPI) serving `data/raw/shop_orders.json` (D-013) |
| `pipeline/ingest/`, `validate/`, `resolve/` | Layer 1: staging with lineage, typing and validation, customer matching (D-017 to D-021) |
| `pipeline/warehouse/` | Layer 2 and 3: duplicates, star schema, revenue and KPI views, exports, review sheet (D-024 to D-031) |
| `pipeline/alerts/` | Layer 4: alert runner and history (D-034) |
| `pipeline/retrieval/`, `tools/`, `assistant/` | Layer 5: knowledge-base search, read-only data tools, the assistant. **Frozen** at tag `assistant-frozen` (D-041) |
| `pipeline/mask/`, `roles/`, `audit/` | Phase 6: ticket-text masking, demo roles, audit log (D-043 to D-045) |
| `dashboard/` | Streamlit pages; they only select from warehouse views (D-032) |
| `config/` | Every threshold, rule and setting (below) |
| `semantic/` | Data dictionary and metric definitions (D-022) |
| `eval/` | Evaluation scripts, questions and results; the only code allowed to read the answer key |
| `tests/` | 427 tests (`uv run pytest`) |
| `Dockerfile`, `docker-compose.yml` | Containers (D-046) |

## 2. Running it

**Locally** (Mac or Linux, with uv):

```
uv sync
uv run python -m generator.run                                   # once, or to start again from the seed
uv run uvicorn shop_api.app:app --host 127.0.0.1 --port 8765     # second terminal
uv run python -m pipeline.run                                    # ingest -> ... -> alerts, KB index, audit head
uv run streamlit run dashboard/app.py                            # http://127.0.0.1:8501
uv run python -m pipeline.audit.verify                           # audit chain check
```

**In Docker**: `docker compose build`, `docker compose run --rm pipeline`, `docker compose up -d dashboard`. Data
lives in Docker volumes, never in the host's `data/` folder ([README](../README.md#running-in-docker-phase-6)).

**The assistant** needs `ANTHROPIC_API_KEY` in `.env` (copy `.env.example`; never commit `.env`). Every call is
logged with its cost in `data/assistant/calls.jsonl`, and calls stop at £5 (D-037).
`uv run python -m pipeline.assistant.ask --spend` shows the total so far.

## 3. Changing things

| To change | Edit | Then | Notes |
|---|---|---|---|
| The as-of date | `config/pipeline.yaml` `as_of_moment`, `config/validation.yaml` `as_of_date`, `config/warehouse.yaml` `data_window` | `python -m pipeline.run` | The KPI windows (next 90 days, at-risk quarters) are set in `config/kpi_thresholds.yaml` and `config/at_risk.yaml` and must move with it (D-002, D-030) |
| KPI thresholds (near breach, unusual volume, onboarding) | `config/kpi_thresholds.yaml` | `python -m pipeline.run` | Each threshold carries its source and a plain-English definition; `tests/test_kpi_thresholds.py` checks them (D-030) |
| The at-risk pattern | `config/at_risk.yaml` | `python -m pipeline.run`, `python -m eval.at_risk_eval` | A test keeps it equal to the documented pattern (D-028) |
| Alert rules | `config/alert_rules.yaml` | `python -m pipeline.alerts.run` | A rule may only read a `v_` view; logic stays in the views (D-034) |
| Review-queue decisions | Fill `data/warehouse/review_sheet.csv` (from `python -m pipeline.warehouse.review_sheet`), copy rows to `config/review_decisions.csv` | `python -m pipeline.warehouse.apply_review` | Shows the before and after effect; a blank decision means "can't decide"; logged in the audit log (D-029, D-045). The 14 decisions now in the file were proposed by the reviewing AI and accepted by the author; they are recorded with reviewer "Author". A real deployment would have the person who decides enter their own name |
| Who sees what | `config/roles.yaml` | Restart the dashboard | Unknown pages, tools or rules are refused when loading; `tests/test_roles.py` covers every role (D-044) |
| Validation rules and vocabularies | `config/validation.yaml`, `config/vocabulary.yaml` | `python -m pipeline.run`, `python -m eval.validation_report` | (D-018, D-022) |
| Customer-matching thresholds | `config/resolution.yaml` | `python -m eval.resolution_eval tune` on the **dev half only**, then `report` | Changing a threshold after seeing held-out results is tuning on the held-out half; use a fresh split (D-019, D-020) |
| Audit retention | `config/audit.yaml` `retention_days` | `python -m pipeline.audit.verify` | Reported only; nothing is deleted automatically (D-045) |
| The assistant's prompt, search or tools | `pipeline/assistant/`, `pipeline/retrieval/`, `pipeline/tools/` | A **new** set of held-out questions | These are frozen: a test fails if they differ from the `assistant-frozen` tag. The existing held-out questions have been used, so any change needs fresh, unseen questions before its score means anything (D-041, D-042) |
| Masking rules | `pipeline/mask/text.py` | `python -m eval.masking_eval` (dev half) | The held-out half has been scored once (`--final` refuses again); a change needs a fresh split or new data (D-043) |

## 4. Adding a data source

1. **Ingest:** add a reader in `pipeline/ingest/` that writes a text-only staging table with the lineage columns (`_source_system`, `_source_file`, `_source_row`, `_ingested_at`), following `files.py` (D-017).
2. **Validate:** add its rules to `config/validation.yaml` and a typing step in `pipeline/validate/` (D-018).
3. **Match customers:** if it names customers, add it to `pipeline/resolve/` with the same threshold, margin and review-queue rules (D-020).
4. **Warehouse:** add a fact or dimension in `pipeline/warehouse/load.py`, then views in `views.py` or `kpis.py`.
5. **Document:** add every column to `semantic/data_dictionary.yaml`; `tests/test_semantic.py` fails until each column has an entry (D-022).
6. **Personal data:** if it holds free text or contact details, flag those columns `personal_data: true`, keep them out of exports and the dashboard, and mask them (D-043).
7. **Test and record:** add tests, and a decision entry in `DECISIONS.md` with the choice, alternatives and reason.

## 5. Checks before trusting a change

```
uv run pytest                                  # all 427 tests
uv run python -m eval.reconciliation           # revenue, all five lines
uv run python -m eval.resolution_eval report   # matching on the held-out half
uv run python -m eval.sla_eval                 # SLA rates against the generator
uv run python -m eval.kpi_checks               # BQ-05 and BQ-12 explained
uv run python -m eval.assistant_eval --cache-only   # assistant scores from the cache, no API calls
uv run python -m pipeline.audit.verify         # audit chain intact
```

The full test run takes about eight minutes. Two rules hold throughout: no pipeline code reads the answer key
(tested), and every number shown or reported comes from a re-runnable script.

## 6. Suggested next steps

In rough order of value, from the limitations in the [README](../README.md#known-limitations) and
[`docs/phase6_summary.md`](phase6_summary.md#known-limitations):

1. **Build BQ-15**, the weekly operations summary (step 5.6, deferred).
2. **Close the "partly met" questions:** dashboard pages for the BQ-03, 05, 08, 09, 10 and 12 views, and data tools for BQ-03, 08, 09, 10 and 12, each with new evaluation questions.
3. **Assistant search of masked past tickets** (BQ-11), with a fresh held-out question set.
4. **Real data:** measure masking and matching on a reviewed sample of real records; expect lower scores than on synthetic data.
5. **Production hardening:** sign-in and server-side access control in place of demo roles; audit entries or head hashes sent to write-once storage; image vulnerability scanning; an updated base-image digest.
6. **Checker improvements:** verify simple calculated figures in assistant answers (T17, DH01).
