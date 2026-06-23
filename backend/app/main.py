"""FastAPI application — the slimmed two-endpoint service.

- ``POST /api/ingest/...`` — 1С pushes order history into the DB.
- ``POST /api/analyze``    — operator posts the draft order, gets recommendations.

Auth: client requests a JWT at POST /api/auth/token (client_id + secret), then
calls protected endpoints with it (scopes: ingest / analyze). See core/security.py.

Run (from repo root, DB up):
    uvicorn backend.app.main:app --port 5050
"""
import logging

from fastapi import FastAPI

from backend.app.api import analysis, auth, ingest
from backend.app.core.config import settings

app = FastAPI(title="AG Sales Analytics API", version="0.3.0")

if not settings.auth_jwt_secret:
    logging.getLogger("uvicorn.error").warning(
        "AUTH DISABLED: AUTH_JWT_SECRET is empty — the API is OPEN. "
        "Set it (and AUTH_CLIENTS) before exposing the service publicly."
    )

for module in (auth, ingest, analysis):
    app.include_router(module.router)


@app.get("/health", tags=["meta"])
async def health():
    return {"status": "ok"}
