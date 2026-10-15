# Northgate Ops Intel

A small working prototype of an AI-assisted operational intelligence platform for a
**fictional** managed IT services provider, Northgate IT Services Ltd.

> **All data in this project is synthetic.** Northgate IT Services Ltd is a fictional company.
> No real company's name, branding or data is used. "Northgate IT Services Ltd" is a
> made-up company name and has no connection to any real company called Northgate,
> including Northgate plc in Darlington.

Status: in progress. Full documentation will be added in Phase 7.

## Phase 0: Business understanding

| Document | What it covers |
|---|---|
| [`docs/01_business_processes.md`](docs/01_business_processes.md) | Company fact sheet (business lines, SLAs, revenue sources, as-of date, ID formats); four core processes with Mermaid maps and the decisions managers make |
| [`docs/02_as_is_architecture.md`](docs/02_as_is_architecture.md) | Source system inventory, known data quality problems (QI-01 to QI-11), as-is data flow and pain points (PP-01 to PP-08) |
| [`docs/03_requirements.md`](docs/03_requirements.md) | 16 business questions (BQ-01 to BQ-16) with acceptance tests, out-of-scope list, traceability matrix |

Design decisions are recorded in [`DECISIONS.md`](DECISIONS.md).

## Building the synthetic data

```
uv sync
uv run python -m generator.run      # builds data/clean, data/raw and data/ground_truth from scratch
uv run pytest -q
```

`data/raw/` holds the messy files the platform ingests and is written only by
`generator.run` (through `generator.messiness`). Single generators such as
`uv run python -m generator.helpdesk` write clean files to `data/clean/` for inspection
and never touch `data/raw/`. The mock shop API serves `data/raw/shop_orders.json`:
`uv run uvicorn shop_api.app:app --host 127.0.0.1 --port 8765`.

## Ingesting the raw data (Layer 1)

```
uv run uvicorn shop_api.app:app --host 127.0.0.1 --port 8765   # in one terminal
uv run python -m pipeline.ingest.run                           # in another
```

Writes text-only staging tables with lineage to `data/staging/` (D-017).

```
uv run python -m pipeline.validate.run     # typed tables and findings in data/validated/ (D-018)
uv run python -m eval.validation_report    # scores against the answer key and the unseen-values fixture
```

```
uv run python -m pipeline.resolve.run          # crosswalk and review queue in data/resolved/ (D-019, D-020)
uv run python -m eval.resolution_eval tune     # dev half only: score each candidate threshold
uv run python -m eval.resolution_eval report   # held-out scores, named cases, review queue, BQ-06 payoff
```

```
uv run python -m pipeline.warehouse.dedup      # duplicate orders and bills marked in data/warehouse/ (D-024)
uv run python -m eval.dedup_eval               # scores by time window, genuine repeats, BL5 before and after
```

## What is committed and what is generated

<!-- files-table:start -->
| Path | Committed? | What it is | Produced by |
|---|---|---|---|
| `config/generator.yaml` | Yes | Every generator setting (seed, volumes, rates) | Hand-written |
| `docs/`, `DECISIONS.md`, `README.md` | Yes | Business understanding, decisions, this file | Hand-written (this table by `generator.run`) |
| `config/vocabulary.yaml`, `semantic/data_dictionary.yaml`, `semantic/metrics.yaml` | Yes | Ticket-category vocabulary, data dictionary with personal-data flags, metric definitions | Hand-written (D-022) |
| `generator/`, `shop_api/`, `tests/` | Yes | Code and tests | Hand-written |
| `knowledge_base/*.md`, `knowledge_base/_meta.yaml` | Yes | 50 KB documents and their ground truth | Hand-written (D-014) |
| `pyproject.toml`, `uv.lock`, `.python-version` | Yes | Exact dependency versions (D-005) | `uv` |
| `data/clean/` | No | Clean sources before any quality problems | `python -m generator.run` (step 1) |
| `data/raw/` | No | Messy sources the platform ingests | `python -m generator.run` (step 2) |
| `data/ground_truth/` | No | Answer keys: true accounts, known issues, KB links | `python -m generator.run` (step 2) |
| `data/staging/` | No | Ingested text-only tables with lineage | `python -m pipeline.ingest.run` |
| `data/validated/` | No | Typed tables and validation findings | `python -m pipeline.validate.run` |
| `data/resolved/` | No | Crosswalk of every source record to a CRM account, and the review queue | `python -m pipeline.resolve.run` |
| `data/warehouse/` | No | Duplicate marks now; the DuckDB warehouse and exports from step 3.3 | `python -m pipeline.warehouse.dedup` |
| `.env` | No | API key (never committed) | You |

Everything under `data/` is reproduced exactly from the seed in `config/generator.yaml`.
<!-- files-table:end -->
