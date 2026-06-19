from fastapi import APIRouter, Depends

from backend.app import repository as repo
from backend.app.core.security import require_auth
from backend.app.schemas import SyncStatusOut

router = APIRouter(prefix="/api", tags=["sync"], dependencies=[Depends(require_auth)])


@router.get("/sync/status", response_model=SyncStatusOut)
async def sync_status():
    return await repo.get_sync_state()
