"""Effect витрина — выгрузка данных для BI (read-only).

Отдаёт витрину воронки эффекта (``effect_funnel``, грейн order_id × n3_id) и журнал
выданных рекомендаций (``recommendation_log``). BI по ним собирает воронку и
атрибуцию A/B/C (джойн журнала и витрины по (order_id, n3_id)), резолвя GUID в
наименования из справочников 1С. Требует scope ``effect``.
"""
from datetime import date

from fastapi import APIRouter, Depends, Query

from backend.app import repository as repo
from backend.app.core.security import require_scope
from backend.app.schemas import (
    EffectFunnelPage,
    RecommendationLogPage,
)

router = APIRouter(
    prefix="/api/effect",
    tags=["effect"],
    dependencies=[Depends(require_scope("effect"))],
)


@router.get("/funnel", response_model=EffectFunnelPage)
async def effect_funnel(
    niche: str | None = Query(None, description="фильтр по отрасли (GUID)"),
    period_from: date | None = Query(None, description="месяц заказа ≥ (YYYY-MM-DD)"),
    period_to: date | None = Query(None, description="месяц заказа ≤ (YYYY-MM-DD)"),
    limit: int = Query(1000, ge=1, le=10000),
    offset: int = Query(0, ge=0),
):
    """Строки витрины эффекта для BI (пагинация limit/offset)."""
    items, count = await repo.get_effect_funnel(niche, period_from, period_to, limit, offset)
    return {"items": items, "limit": limit, "offset": offset, "count": count}


@router.get("/recommendations", response_model=RecommendationLogPage)
async def effect_recommendations(
    since_id: int | None = Query(None, ge=0, description="инкрементально: id больше указанного"),
    kind: str | None = Query(None, description="'forgotten' | 'niche'"),
    limit: int = Query(1000, ge=1, le=10000),
    offset: int = Query(0, ge=0),
):
    """Журнал выданных рекомендаций для BI (пагинация; since_id — инкрементальная выгрузка)."""
    items, count = await repo.get_recommendation_log(since_id, kind, limit, offset)
    return {"items": items, "limit": limit, "offset": offset, "count": count}
