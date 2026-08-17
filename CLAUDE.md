# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Russian-language sales-analytics service for metal-rolling (металлопрокат) managers. Given a client's order history and the current draft order, it returns two kinds of cross-sell recommendations: (1) **forgotten** products this client usually buys but is missing or under-ordering now, and (2) **niche** products that similar clients in the same отрасль buy. All domain text (ниши, priorities, error messages) is Russian.

The repo is **mid-rewrite**. Two apps coexist:

- **`backend/`** — the active service: a FastAPI + PostgreSQL, GUID-keyed, 1С-push-integration rewrite. **All new work goes here.**
- **repo root** (`app.py`, `data_store.py`, `analyzer.py`, `column_mapper.py`, `ai_chat.py`, `templates/`, `static/`) — the **legacy** Flask/pandas MVP (Excel upload, in-memory globals, AI chat). Kept running via `docker-compose.legacy.yml` until cutover. Don't build new features on it; see the "What replaces what" table in `backend/README.md`.

The rest of this file describes the **`backend/` service** unless it says "legacy".

## Running

Everything runs as Python modules from the **repo root** (imports are `backend.app.*`), inside Docker. The unified stack is the root `docker-compose.yml`:

```bash
docker compose up -d --build        # API + OpenAPI docs at http://localhost:5050/docs
```

This starts four services: `db` (Postgres 16, **not** published to the host), `migrate` (one-shot: applies the schema then seeds MOCK data — `python -m backend.db.schema && python -m backend.sync.seed_mock`), `api` (uvicorn on 5050), and `worker` (rebuilds aggregates on an interval).

- `docker compose down -v` wipes the DB volume. **Schema changes require this** — there are no incremental migrations (see below).
- The `migrate` step seeds synthetic history (`sync/mock_source.py`) until the real 1С feed lands. `KNOWN_CLIENT = clnt0000-…-000000000000` is mock client #1 (deterministic, `seed=42`).
- Legacy MVP: `docker compose -f docker-compose.legacy.yml up --build` (serves the old Flask app on 5050; needs `.env` with `OPENROUTER_API_KEY`).

### Env

Copy `backend/.env.example`. Key settings (`backend/app/core/config.py`): `DATABASE_URL`, `AUTH_JWT_SECRET` (**empty = auth OPEN, dev only**), `AUTH_CLIENTS` (JSON list of `{client_id, client_secret, scopes}`), `REFRESH_INTERVAL_MINUTES`.

### Tests

```bash
# Pure rule/auth tests — no DB:
pytest -c backend/pytest.ini backend/tests/test_analyzer.py backend/tests/test_auth.py

# Integration — needs a reachable, schema-applied DB (set DATABASE_URL or TEST_DATABASE_URL):
python -m backend.db.schema
pytest -c backend/pytest.ini -m integration
```

Integration tests seed mock data themselves via `sync.seed_mock.seed()`. There is no linter configured. CI (GitLab, `.gitlab-ci.yml`) runs the full suite against an ephemeral Postgres and **deploys on branch `master`** to the stand via `docker compose`.

## Architecture

Two-endpoint **push** service. 1С owns the data; the service is read-only toward 1С.

```
1С ──POST /api/ingest/order-lines (delta)──► order_lines (raw) ──commit/worker──► materialized views
                                                                                   client_dim, niche_dim, products,
                                                                                   client_profile_n4, niche_profile_n4
                                                                                        │
operator/1С ──POST /api/analyze {client + draft lines}──► services.analyze ──► domain/analyzer (pure) ◄── repository (async SQLAlchemy Core)
```

- **`app/main.py`** — mounts three routers: `auth`, `ingest`, `analysis`. Warns loudly if `AUTH_JWT_SECRET` is empty.
- **`app/api/`** — the surface. `auth.py`: `POST /api/auth/token` (client_id + secret → scoped JWT). `ingest.py`: `POST /api/ingest/order-lines` (write delta), `/commit` (rebuild aggregates after a load), `/refresh` (force rebuild) — all require the `ingest` scope. `analysis.py`: `POST /api/analyze` — requires the `analyze` scope. Plus `GET /health`.
- **`app/services.py`** — orchestration: resolves draft lines to N3, fetches the client/niche profiles via the repository, runs both analyzer functions, assembles the response. Stateless.
- **`app/domain/analyzer.py`** — the two recommendation algorithms as **pure functions** over already-fetched profile rows (no I/O). Both match at **N3** granularity, sort by frequency, return `TOP_N`. Tunable thresholds are constants at the top.
- **`app/repository.py`** — async data access over PostgreSQL using **SQLAlchemy Core** (not the ORM). `ingest_order_lines` upserts by replacing every `order_num` in the batch (delete-then-insert; `deleted: true` just removes). `refresh_aggregates` rebuilds the materialized views (CONCURRENTLY once populated, under AUTOCOMMIT). Read helpers are scoped to one client/niche.
- **`app/schemas.py`** — Pydantic models = the API contract (request validation + response shape).
- **`app/core/`** — `config.py` (settings), `security.py` (JWT issue/verify + `require_scope` dependency factory).
- **`app/db/`** — `session.py` (one shared async engine), `tables.py` (Core `Table` objects for typed queries — reading only; the schema is not defined here).
- **`db/schema.sql`** + **`db/schema.py`** — the **entire schema** (tables, indexes, materialized views), applied idempotently (`CREATE … IF NOT EXISTS`). **No Alembic / no migrations** — change `schema.sql` and recreate the volume. `main.py` is the source of truth for the endpoint surface.
- **`sync/`** — `worker.py` (APScheduler loop rebuilding aggregates every `REFRESH_INTERVAL_MINUTES`), `mock_source.py` (synthetic GUID-keyed rows), `seed_mock.py` (pushes them through the real ingest path — dev only).

## Domain model (key invariants)

- **Identity is by GUID**: `client_id`, `n3_id`, `n4_id` (and `order_num` for orders). **Product/subgroup names are NOT stored** — 1С resolves display names by GUID. `client_name` is the only carried display field; `niche` (= Отрасль) is a plain string.
- Hierarchy is **N1 → N2 → N3 → N4**, where **N3 = подгруппа** and **N4 = товар/SKU**. Analysis matches at **N3**; `n4_id` is carried through for reference. Draft-order lines may send `n3_id` directly or `n4_id` (n3 resolved server-side from the `products` matview).
- **"Frequency"** = *share of the client's (or niche's) orders that contain the item*, not a raw count (`frequency_pct` / `niche_freq_pct`, computed in `schema.sql`).
- A client's niche is the **mode** (most common) niche across their rows (`client_dim` matview).
- History is a **delta owned by 1С**; there are no deletions in practice. Aggregates are **materialized on sync**, never computed per-request.
- Quantities (`qty`) are tonnes, kept as `numeric`, rounded to 3 decimals.

## Conventions

- Adding a data field means threading it through the whole pipe: `schema.sql` (raw column + every matview that carries it) → `db/tables.py` (Core columns) → `repository.py` row-mappers → `schemas.py` (Pydantic) → `domain/analyzer.py` / `services.py`. Then recreate the DB volume.
- API errors are `HTTPException(status, detail="<Russian message>")`; the response shape is `{"detail": "…"}`.
- Auth model: clients never send raw secrets to protected endpoints — they exchange `client_id`+`client_secret` at `/api/auth/token` for a short-lived JWT carrying scopes (`ingest`, `analyze`), then send `Authorization: Bearer <token>`. Empty `AUTH_JWT_SECRET` bypasses all checks (dev).
- The 1С integration contract lives in `docs/` (`onec_api.md` is the current stand format; `onec_integration.md`, `onec_ingest_contract.md`, `onec_contract.md`). Keep code and these docs in sync when the contract changes.
