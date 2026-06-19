from fastapi import APIRouter, Depends

from backend.app import repository as repo
from backend.app.core.security import require_auth
from backend.app.schemas import ClientInfoOut, ClientOut

router = APIRouter(prefix="/api", tags=["clients"], dependencies=[Depends(require_auth)])


@router.get("/clients", response_model=list[ClientOut])
async def list_clients():
    return await repo.get_clients()


@router.get("/clients/{client_guid}/info", response_model=ClientInfoOut)
async def client_info(client_guid: str):
    return {
        "niche": await repo.get_client_niche(client_guid),
        "total_orders": await repo.get_client_total_orders(client_guid),
    }
