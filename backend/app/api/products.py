from fastapi import APIRouter, Depends

from backend.app import repository as repo
from backend.app.core.security import require_auth
from backend.app.schemas import ProductN4Out

router = APIRouter(prefix="/api", tags=["products"], dependencies=[Depends(require_auth)])


@router.get("/products/n3", response_model=list[str])
async def products_n3():
    return await repo.get_products_n3()


@router.get("/products/n4", response_model=list[ProductN4Out])
async def products_n4(n3: str):
    return await repo.get_products_n4(n3)
