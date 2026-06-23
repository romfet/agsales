"""Pydantic models = the API contract (request validation + response shape).

Identity by GUID: client_id, n3_id, n4_id. Names (*_name) are display-only.
Order key = order_num.
"""
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
    """One order-line record (grain: order × product), pushed by 1С (delta)."""

    order_num: str
    order_date: str | None = None  # ISO YYYY-MM-DD
    client_id: str
    client_name: str | None = None
    niche: str | None = None
    n3_id: str
    n4_id: str
    qty: float
    deleted: bool = False


class IngestOrderLinesIn(BaseModel):
    items: list[IngestOrderLine] = Field(default_factory=list)


class IngestResult(BaseModel):
    accepted: int
    orders_affected: int | None = None


# --- Analyze (operator → us; endpoint 2) ---
class OrderLineIn(BaseModel):
    """A line of the current (draft) order. Analyzer works at N3 level: send
    ``n4_id`` (n3 resolved server-side) or ``n3_id`` directly."""

    n4_id: str | None = None
    n3_id: str | None = None
    qty: float = 0.0


class AnalyzeIn(BaseModel):
    client_id: str
    lines: list[OrderLineIn] = Field(default_factory=list)


class ForgottenItem(BaseModel):
    type: str
    n3_id: str
    n4_id: str | None = None
    order_count: int
    total_orders: int
    frequency_pct: float
    avg_qty: float
    current_qty: float | None = None
    priority: str


class NicheItem(BaseModel):
    n3_id: str
    n4_id: str | None = None
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
    client_id: str
    niche: str | None = None
    analysis1_forgotten: list[ForgottenItem]
    analysis2_niche: list[NicheItem]
