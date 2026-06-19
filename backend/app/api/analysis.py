from fastapi import APIRouter, Depends, HTTPException

from backend.app import services
from backend.app.core.security import require_auth
from backend.app.schemas import AnalyzeIn, AnalyzeOut

router = APIRouter(prefix="/api", tags=["analysis"], dependencies=[Depends(require_auth)])


@router.post("/analyze", response_model=AnalyzeOut)
async def analyze(payload: AnalyzeIn):
    if not payload.order_lines:
        raise HTTPException(status_code=400, detail="Заявка пуста")
    order_lines = [line.model_dump() for line in payload.order_lines]
    return await services.analyze(payload.client_guid, order_lines)
