from fastapi import APIRouter, Depends, HTTPException

from backend.app import repository as repo
from backend.app.core.security import require_auth
from backend.app.schemas import OrderDetailOut, OrderSearchOut

router = APIRouter(prefix="/api", tags=["orders"], dependencies=[Depends(require_auth)])


# Declared before /orders/{order_num} so "search" isn't captured as an order number.
@router.get("/orders/search", response_model=list[OrderSearchOut])
async def search_orders(query: str, client_guid: str | None = None):
    return await repo.search_orders(query, client_guid)


@router.get("/orders/{order_num}", response_model=OrderDetailOut)
async def order_detail(order_num: str):
    result = await repo.get_order_lines(order_num)
    if result is None:
        raise HTTPException(status_code=404, detail="Заказ не найден")
    return result
