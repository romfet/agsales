# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Russian-language Flask MVP for metal-rolling (металлопрокат) sales managers. A manager uploads a CRM export (Excel), builds a customer order, and the app suggests two kinds of cross-sell recommendations: (1) products this client usually buys but forgot, and (2) products that similar clients in the same niche buy. An optional natural-language AI chat helps add order lines, and a stock file can be overlaid to show on-hand/in-transit tonnage. All UI text and column names are in Russian.

## Running

The app is built to run **in Docker** — `UPLOAD_FOLDER` is hardcoded to `/app/uploads` (`app.py`), so running `python app.py` directly on a host will fail to create that path. Use:

```bash
docker compose up --build        # serves on 127.0.0.1:5050
```

Requires a `.env` file (gitignored) with `OPENROUTER_API_KEY` for the AI chat (and optionally `OPENROUTER_MODEL`, default `google/gemini-2.5-flash`). Without the key the app still works; chat just returns a "not configured" message.

Production is served via nginx (`nginx-ag.romfet.space`) reverse-proxying to port 5050 at `ag.romfet.space`.

There is no test suite, linter, or build step configured.

## Architecture

Single-process Flask app holding **all data in module-level global state in `data_store.py`** — there is no database. Uploading a new file replaces the in-memory state. State does not survive a restart; the uploaded file persists at `uploads/current_data.xlsx` but is not auto-reloaded on boot.

Request flow and module responsibilities:

- **`app.py`** — Flask routes only (pages + JSON API). Thin; delegates everything to the modules below. The two-step upload is: `POST /upload` (detect mapping, file saved as `current_data.xlsx`) → `POST /upload/confirm` (apply confirmed mapping, load into state).
- **`column_mapper.py`** — Excel arrives with arbitrary column names. `detect_mapping()` proposes which file column fills each of 9 internal schema slots (`EXPECTED_COLUMNS`) using three scored strategies — exact name, keyword/fuzzy name, and content analysis (dtype, cardinality, value patterns like hierarchy dots) — then does a greedy one-to-one assignment. The frontend shows this for the user to confirm/correct.
- **`data_store.py`** — On `load_excel()`, validates required columns, then precomputes all aggregate DataFrames the rest of the app reads: client profiles and niche profiles, each at both **N3 (подгруппа)** and **N4 (товар)** granularity. Everything downstream queries these cached frames, never the raw rows (except order lookup/search). Stock data lives in a separate parallel state (`_stock_by_n4`, loaded via `load_stock()`) keyed by N4 name.
- **`analyzer.py`** — The two recommendation algorithms, both operating at N4 level and returning the top 5 by frequency. `analyze_forgotten` compares the current order against the client's own history; `analyze_niche` compares against niche-wide popularity. Both call `_attach_stock()` to annotate each suggestion with tonnage. Tunable thresholds are constants at the top of the file.
- **`ai_chat.py`** — Stateless wrapper over the OpenRouter chat API. Injects the **current product catalog** into the system prompt, forces a JSON response, then **validates every returned suggestion against the real catalog** (with N4 fuzzy fallback) before sending it back — the model can only ever return real N3/N4 names.

## Domain model (key invariants)

- The product hierarchy is **N1 → N2 → N3 → N4**, where N3 = подгруппа (subgroup) and N4 = товар (individual product/SKU). Only N3, N4, plus client/niche/order#/date/quantity are required (`REQUIRED_COLUMNS`); N1/N2 are optional.
- "Frequency" means *share of the client's (or niche's) orders that contain this item*, not a raw count — see `frequency_pct` / `niche_freq_pct` in `data_store.py`.
- A client's niche is the **mode** (most common) niche value across their rows.
- Quantities are tonnes and kept as floats rounded to 3 decimals.

## Conventions

- Column names are the source of truth and are **Russian string constants** (`COL_*` in `data_store.py`). When adding a field, thread it through: `EXPECTED_COLUMNS` (mapper) → `REQUIRED_COLUMNS`/aggregates (store) → analyzer/templates.
- API errors are returned as `{"error": "<Russian message>"}` with a 4xx/5xx status; the frontend expects that shape.
- `templates/` (Jinja, `base.html` + pages) and `static/style.css` are the entire frontend; pages talk to the JSON API via fetch.
