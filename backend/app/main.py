"""FastAPI application — the slimmed two-endpoint service.

- ``POST /api/ingest/...`` — 1С pushes order history into the DB.
- ``POST /api/analyze``    — operator posts the draft order, gets recommendations.

Auth: client requests a JWT at POST /api/auth/token (client_id + secret), then
calls protected endpoints with it (scopes: ingest / analyze). See core/security.py.

Run (from repo root, DB up):
    uvicorn backend.app.main:app --port 5050
"""
from fastapi import FastAPI

from backend.app.api import analysis, auth, ingest

app = FastAPI(title="AG Sales Analytics API", version="0.3.0")

for module in (auth, ingest, analysis):
    app.include_router(module.router)


@app.get("/health", tags=["meta"])
async def health():
    return {"status": "ok"}
