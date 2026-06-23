"""Pydantic models = the API contract (request validation + response shape)."""
from __future__ import annotations

from pydantic import BaseModel, Field


# --- Auth (token-request flow) ---
class TokenRequest(BaseModel):
    client_id: str
    client_secret: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    scope: str


# --- Ingest (1С → us; endpoint 1) ---
class IngestOrderLine(BaseModel):
    """One order-line record (grain: order × item), pushed by 1С.

    Flat/denormalized — 1С joins on its side. A record with ``deleted=true``
    (or simply an order_guid no longer sent with lines) removes that order.
    """

    order_guid: str
    order_num: str
    order_date: str | None = None  # ISO YYYY-MM-DD
    client_guid: str
    client_name: str
    niche: str | None = None
    n1: str | None = None
    n2: str | None = None
    n3: str
    n4: str
    item_guid: str
    qty: float
    deleted: bool = False


class IngestOrderLinesIn(BaseModel):
    items: list[IngestOrderLine] = Field(default_factory=list)


class IngestResult(BaseModel):
    accepted: int
    orders_affected: int | None = None


# --- Analyze (operator → us; endpoint 2) ---
class OrderLineIn(BaseModel):
    """A line of the current (draft) order. The analyzer works at N3 level:
    send ``item_guid`` (n3 resolved server-side) or ``n3`` directly; ``qty``
    feeds the under-ordering check."""

    item_guid: str | None = None
    n3: str | None = None
    qty: float = 0.0


class AnalyzeIn(BaseModel):
    client_guid: str
    lines: list[OrderLineIn] = Field(default_factory=list)


class ForgottenItem(BaseModel):
    type: str
    n3: str
    n4: str | None = None
    order_count: int
    total_orders: int
    frequency_pct: float
    avg_qty: float
    current_qty: float | None = None
    priority: str


class NicheItem(BaseModel):
    n3: str
    n4: str | None = None
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


class AnalyzeOut(BaseModel):
    client_guid: str
    niche: str | None = None
    analysis1_forgotten: list[ForgottenItem]
    analysis2_niche: list[NicheItem]
