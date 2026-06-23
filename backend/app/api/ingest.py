"""Endpoint 1 — ingest: 1С pushes the order history; we write it to the DB.

Requires the ``ingest`` scope. Aggregates are NOT rebuilt per batch (too
expensive). After the daily load 1С calls ``/commit`` to finalize (rebuild
aggregates); the worker also rebuilds them on an interval, and ``/refresh``
forces it on demand.
"""
from fastapi import APIRouter, Depends

from backend.app import repository as repo
from backend.app.core.security import require_scope
from backend.app.schemas import IngestOrderLinesIn, IngestResult

router = APIRouter(
    prefix="/api/ingest",
    tags=["ingest"],
    dependencies=[Depends(require_scope("ingest"))],
)


@router.post("/order-lines", response_model=IngestResult)
async def ingest_order_lines(body: IngestOrderLinesIn):
    return await repo.ingest_order_lines([i.model_dump() for i in body.items])


@router.post("/commit")
async def ingest_commit():
    """Finalize the daily load: rebuild aggregates from the current data."""
    await repo.refresh_aggregates()
    return {"status": "ok", "rows": await repo.count_order_lines()}


@router.post("/refresh")
async def ingest_refresh():
    """Force an immediate aggregate rebuild (ops/dev; the worker also does it on a timer)."""
    await repo.refresh_aggregates()
    return {"status": "ok"}
