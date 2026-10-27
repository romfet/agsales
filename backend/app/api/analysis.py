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
    # Пустой список ИЛИ строки без единого идентификатора (n3_id/n4_id) — заявку не
    # разобрать: иначе набор позиций пуст и ВСЁ, что клиент обычно берёт, ложно
    # помечается «missing».
    if not payload.lines or all(not (l.n3_id or l.n4_id) for l in payload.lines):
        raise HTTPException(status_code=400, detail="Заявка пуста")
    if payload.period_from and payload.period_to and payload.period_from > payload.period_to:
        raise HTTPException(status_code=422, detail="period_from не может быть позже period_to")
    return await services.analyze(
        payload.client_id,
        [l.model_dump() for l in payload.lines],
        order_id=payload.order_id,
        period_from=payload.period_from,
        period_to=payload.period_to,
    )
