# Backend

Sales-analytics service. Replaces the Excel-upload + in-memory pandas MVP (repo
root) with a PostgreSQL-backed, async data layer fed by 1С (read-only toward 1С).

A slim **two-endpoint push service**: 1С pushes order history to `/api/ingest/*`;
an operator posts a draft order to `/api/analyze` and gets recommendations back.
Identity is by **GUID** (`client_id` / `n3_id` / `n4_id`); names are display-only
(1С resolves them). See the 1С contract in
[`../docs/onec_integration.md`](../docs/onec_integration.md) (+ `onec_api.md`).

## Architecture

```
1С ──POST /api/ingest/order-lines (delta)──► order_lines (raw) ──commit/worker──► materialized views
                                                                                   client_dim / niche_dim / products
                                                                                   client_profile_n4 / niche_profile_n4
                                                                                        │
operator ──POST /api/analyze {client + draft lines}──► services.analyze ──► domain/analyzer (pure) ◄── repository (async SQLAlchemy Core)
```

Aggregates are **materialized on sync** (rebuilt after a load's `/commit`, and by
the worker on an interval), then read per-request scoped to one client/niche — no
full-dataset work at request time, no pandas.

## Run locally

The whole stack (Postgres + schema/seed + API + worker) is the **repo-root**
`docker-compose.yml`. From the repo root:

```bash
docker compose up -d --build        # OpenAPI docs at http://localhost:5050/docs
```

`migrate` applies the schema and seeds **mock** history until the real 1С feed
lands. `docker compose down -v` wipes the DB volume (needed for schema changes).

### Or run pieces by hand (from the repo root)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
export DATABASE_URL="postgresql+psycopg://agsales:agsales@localhost:5432/agsales"

docker compose -f backend/docker-compose.yml up -d   # 1. Postgres only
python -m backend.db.migrate                          # 2. Apply migrations (tables + materialized views)
python -m backend.sync.seed_mock                      # 3. Seed mock data + refresh aggregates
uvicorn backend.app.main:app --reload --port 5050     # 4. API
python -m backend.sync.worker                         # (optional) aggregate-refresh loop
```

Schema is a set of **ordered SQL migrations** in `db/migrations/` applied by
`db/migrate.py` (applied versions tracked in `schema_migrations`). `0001_baseline.sql`
is the full schema (idempotent). To change the schema, **add a new `NNNN_*.sql`**
(ALTER/CREATE) — do not edit an applied migration or recreate the DB volume (stand
data must survive).

## API

FastAPI + Pydantic over `services` / `repository`. OpenAPI at `/docs`.

| Method | Path | Scope | Purpose |
|---|---|---|---|
| POST | `/api/auth/token`         | —         | exchange `client_id`+`client_secret` for a scoped JWT |
| POST | `/api/ingest/order-lines` | `ingest`  | push order-line delta (upsert by `order_num`) |
| POST | `/api/ingest/commit`      | `ingest`  | finalize a load: rebuild aggregates |
| POST | `/api/ingest/refresh`     | `ingest`  | force an immediate aggregate rebuild |
| POST | `/api/analyze`            | `analyze` | both analyses for a client + draft order |
| GET  | `/health`                 | —         | liveness |

**Auth** is a token-request flow (`core/security.py`): a client POSTs its
`client_id`+`client_secret`, gets a server-signed HS256 JWT carrying its scopes,
then calls protected endpoints with `Authorization: Bearer <token>`. Clients are
registered via `AUTH_CLIENTS` (JSON). **Empty `AUTH_JWT_SECRET` = auth OPEN
(dev only).**

## Tests

```bash
# Pure rule/auth tests — no DB:
pytest -c backend/pytest.ini backend/tests/test_analyzer.py backend/tests/test_auth.py

# Integration (needs DB up + migrated + DATABASE_URL/TEST_DATABASE_URL):
python -m backend.db.migrate
pytest -c backend/pytest.ini -m integration
```

## What replaces what

| Old (repo-root MVP) | New |
|---|---|
| `data_store.py` (pandas globals)        | `app/repository.py` (async SQLAlchemy Core) + materialized views |
| `analyzer.py`                           | `app/domain/analyzer.py` (pure) + `app/services.py` (orchestration) |
| `load_excel` / `column_mapper` / `/upload*` | `sync/` (mock now; 1С push to `/api/ingest/*` in prod) |
| stock match by N4 name                  | (dropped — no stock in the current contract) |
