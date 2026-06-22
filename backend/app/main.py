"""FastAPI application — the slimmed two-endpoint service.

- ``POST /api/ingest/...`` — 1С pushes order history / stock into the DB.
- ``POST /api/analyze``    — operator posts the draft order, gets recommendations.
- ``POST /api/auth/token`` — OAuth2 client-credentials token for the above.

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
