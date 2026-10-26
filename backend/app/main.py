"""FastAPI application — the slimmed two-endpoint service.

- ``POST /api/ingest/...`` — 1С pushes order history into the DB.
- ``POST /api/analyze``    — operator posts the draft order, gets recommendations.

Auth: client requests a JWT at POST /api/auth/token (client_id + secret), then
calls protected endpoints with it (scopes: ingest / analyze / effect). See
core/security.py. ``effect`` gates the BI read endpoints under /api/effect.

Run (from repo root, DB up):
    uvicorn backend.app.main:app --port 5050
"""
import logging

from fastapi import FastAPI

from backend.app.api import analysis, auth, effect, ingest
from backend.app.core.config import settings

app = FastAPI(title="AG Sales Analytics API", version="0.3.0")

if not settings.auth_jwt_secret:
    logging.getLogger("uvicorn.error").warning(
        "AUTH DISABLED: AUTH_JWT_SECRET is empty — the API is OPEN. "
        "Set it (and AUTH_CLIENTS) before exposing the service publicly."
    )

logging.getLogger("uvicorn.error").info(
    "ingest_mode=%s (shrink=%s, merge_on_commit=%s)",
    settings.ingest_mode, settings.ingest_shrink_mode, settings.ingest_merge_on_commit,
)

for module in (auth, ingest, analysis, effect):
    app.include_router(module.router)


@app.get("/health", tags=["meta"])
async def health():
    return {"status": "ok"}
