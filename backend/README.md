# Backend (refactor)

New backend for the sales-analytics service. Replaces the Excel-upload + in-memory
pandas MVP with a PostgreSQL-backed, async data layer fed by 1С (read-only).

**Status: Phase 1 — data layer on mock data.** The 1С client and FastAPI/React
layers come in phases 2–3. See the staged plan in the conversation and the 1С
contract in [`../docs/onec_contract.md`](../docs/onec_contract.md).

## Architecture (phase 1)

```
mock_source ──► order_lines (Postgres) ──► materialized aggregates
                                              client_dim / niche_dim / products
                                              client_profile_n4 / niche_profile_n4
                                                        │
        services.analyze() ── domain/analyzer (pure rules) ◄── repository (async SQLAlchemy Core)
```

Aggregates are **materialized on sync** and read per-request scoped to one
client/niche — no full-dataset work at request time, no pandas. Identity is by
**GUID** (client/item/order), names are for display only.

## Run locally

From the repo root:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt

# 1. Postgres
docker compose -f backend/docker-compose.yml up -d

# 2. Schema (tables + materialized views)
export DATABASE_URL="postgresql+psycopg://agsales:agsales@localhost:5432/agsales"
alembic -c backend/alembic.ini upgrade head

# 3. Seed mock data + refresh aggregates
python -m backend.sync.seed_mock
```

## Tests

```bash
# Pure rule tests — no DB:
pytest backend/tests/test_analyzer.py

# Integration (needs DB up + migrated + DATABASE_URL):
pytest -m integration
```

## What replaces what

| Old | New |
|---|---|
| `data_store.py` (pandas globals) | `app/repository.py` (async SQLAlchemy Core) + materialized views |
| `analyzer.py` | `app/domain/analyzer.py` (pure) + `app/services.py` (orchestration) |
| `load_excel` / `column_mapper` / `/upload*` | `sync/` (mock now; 1С HTTP-service in phase 2) |
| stock match by N4 name | stock keyed by `item_guid` |
