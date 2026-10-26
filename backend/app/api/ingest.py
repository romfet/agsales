"""Endpoint 1 — ingest: 1С pushes the order history; we write it to the DB.

Requires the ``ingest`` scope. INGEST_MODE=direct: each request replaces its
documents at once; aggregates are rebuilt on ``/commit`` (or by the worker).
INGEST_MODE=staged: requests go to staging and the worker merges documents across
batches (see app/staging.py); ``/commit`` answers 202 at once and, only with
INGEST_MERGE_ON_COMMIT=true, queues a merge. ``/refresh`` forces an aggregate rebuild.
"""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from backend.app import repository as repo
from backend.app import staging
from backend.app.core.config import settings
from backend.app.core.security import require_scope
from backend.app.schemas import (
    IngestOrderLinesIn,
    IngestResult,
    IngestShipmentLinesIn,
    IngestShipmentResult,
)

router = APIRouter(
    prefix="/api/ingest",
    tags=["ingest"],
    dependencies=[Depends(require_scope("ingest"))],
)


@router.post("/order-lines", response_model=IngestResult)
async def ingest_order_lines(body: IngestOrderLinesIn):
    return await repo.ingest_order_lines([i.model_dump() for i in body.items])


@router.post("/shipments", response_model=IngestShipmentResult)
async def ingest_shipments(body: IngestShipmentLinesIn):
    """Приём факта отгрузки (upsert по shipment_id)."""
    return await repo.ingest_shipment_lines([i.model_dump() for i in body.items])


@router.post("/commit")
async def ingest_commit(kind: Literal["orders", "shipments"] | None = None):
    """Finalize a load. direct: rebuild aggregates now. staged: 202 — сборку делает
    worker (по паузе в пачках; по commit — только при INGEST_MERGE_ON_COMMIT=true)."""
    if settings.ingest_mode == "staged":
        return JSONResponse(status_code=202, content=await staging.request_commit(kind))
    await repo.refresh_aggregates()
    return {"status": "ok", "rows": await repo.count_order_lines()}


@router.post("/refresh")
async def ingest_refresh():
    """Force an immediate aggregate rebuild (ops/dev; the worker also does it on a timer)."""
    await repo.refresh_aggregates()
    return {"status": "ok"}


@router.get("/stats")
async def ingest_stats():
    """Режим приёма, несобранные пачки, последние сборки."""
    return await staging.get_stats()


@router.get("/merges/{merge_id}")
async def ingest_merge(merge_id: int):
    """Статус и итоги сборки."""
    m = await staging.get_merge(merge_id)
    if m is None:
        raise HTTPException(status_code=404, detail="Сборка не найдена")
    return m
