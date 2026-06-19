"""Pydantic models = the API contract (request validation + response shape)."""
from __future__ import annotations

from pydantic import BaseModel, Field


# --- Clients ---
class ClientOut(BaseModel):
    client_guid: str
    client_name: str
    niche: str | None = None


class ClientInfoOut(BaseModel):
    niche: str | None = None
    total_orders: int


# --- Products ---
class ProductN4Out(BaseModel):
    n4: str
    item_guid: str


# --- Orders ---
class OrderLineOut(BaseModel):
    n3: str
    n4: str | None = None
    item_guid: str | None = None
    qty: float | None = None


class OrderDetailOut(BaseModel):
    client: str
    lines: list[OrderLineOut]


class OrderSearchOut(BaseModel):
    order_num: str
    date: str | None = None
    line_count: int
    client: str


# --- Analyze ---
class OrderLineIn(BaseModel):
    n3: str
    n4: str | None = None
    item_guid: str | None = None
    qty: float = 0.0


class AnalyzeIn(BaseModel):
    client_guid: str
    order_lines: list[OrderLineIn] = Field(default_factory=list)


class ForgottenItem(BaseModel):
    type: str
    n3: str
    n4: str | None = None
    item_guid: str | None = None
    order_count: int
    total_orders: int
    frequency_pct: float
    avg_qty: float
    current_qty: float | None = None
    priority: str
    stock_on: float | None = None
    stock_in_transit: float | None = None


class NicheItem(BaseModel):
    n3: str
    n4: str | None = None
    item_guid: str | None = None
    niche: str
    niche_pct: float
    client_count: int
    total_clients: int
    order_count: int
    total_orders: int
    frequency_pct: float
    client_bought_before: bool
    avg_qty_per_client: float
    priority: str
    stock_on: float | None = None
    stock_in_transit: float | None = None


class AnalyzeOut(BaseModel):
    client_guid: str
    niche: str | None = None
    analysis1_forgotten: list[ForgottenItem]
    analysis2_niche: list[NicheItem]


# --- Sync ---
class SyncStatusOut(BaseModel):
    status: str
    last_sync_at: str | None = None
    row_count: int
    error: str | None = None
