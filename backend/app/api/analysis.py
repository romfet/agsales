"""Endpoint 2 — analyze: operator posts the current (draft) order, gets
recommendations back in the response. Stateless. Requires the ``analyze`` scope.
"""
from fastapi import APIRouter, Depends, HTTPException

from backend.app import services
from backend.app.core.security import require_scope
from backend.app.schemas import AnalyzeIn, AnalyzeOut

router = APIRouter(
    prefix="/api",
    tags=["analysis"],
    dependencies=[Depends(require_scope("analyze"))],
)


@router.post("/analyze", response_model=AnalyzeOut)
async def analyze(payload: AnalyzeIn):
    if not payload.lines:
        raise HTTPException(status_code=400, detail="Заявка пуста")
    return await services.analyze(
        payload.client_guid,
        [l.model_dump() for l in payload.lines],
    )
