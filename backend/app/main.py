"""FastAPI application — JSON API over the async repository/services layer.

Run (from repo root, DB up + seeded):
    uvicorn backend.app.main:app --reload --port 5050
Docs at /docs (OpenAPI), used as the contract for the React SPA in phase 3.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.api import analysis, clients, orders, products, sync
from backend.app.core.config import settings

app = FastAPI(title="AG Sales Analytics API", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)

for module in (clients, products, orders, analysis, sync):
    app.include_router(module.router)


@app.get("/health", tags=["meta"])
async def health():
    return {"status": "ok"}
