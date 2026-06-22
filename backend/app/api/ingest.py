"""Endpoint 1 — ingest: 1С pushes the order history / stock; we write it to the DB.

Requires the ``ingest`` scope. Aggregates are NOT rebuilt per request (too
expensive) — the worker refreshes them on an interval; ``/refresh`` forces it.
"""
from fastapi import APIRouter, Depends

from backend.app import repository as repo
from backend.app.core.security import require_scope
from backend.app.schemas import IngestOrderLinesIn, IngestResult, IngestStockIn

router = APIRouter(
    prefix="/api/ingest",
    tags=["ingest"],
    dependencies=[Depends(require_scope("ingest"))],
)


@router.post("/order-lines", response_model=IngestResult)
async def ingest_order_lines(body: IngestOrderLinesIn):
    return await repo.ingest_order_lines([i.model_dump() for i in body.items])


@router.post("/stock", response_model=IngestResult)
async def ingest_stock(body: IngestStockIn):
    return await repo.ingest_stock([i.model_dump() for i in body.items])


@router.post("/refresh")
async def ingest_refresh():
    """Force an immediate aggregate rebuild (otherwise the worker does it on a timer)."""
    await repo.refresh_aggregates()
    return {"status": "ok"}
