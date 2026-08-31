"""Pydantic models = the API contract (request validation + response shape).

Identity by GUID: client_id, niche, n3_id, n4_id, order_id, shipment_id. Names
(*_name) are display-only. Order key = order_id (GUID); order_num/shipment_num are
display numbers.
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

    order_id: str                  # GUID заказа из 1С — канонический ключ
    order_num: str                 # номер заказа (отображение)
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


# --- Ingest отгрузок (1С → us): факт отгруженного, ключ shipment_id ---
class IngestShipmentLine(BaseModel):
    """Строка отгрузки: как заказ + shipment_id (ключ) + order_id (линк, m2m)."""

    shipment_id: str                    # GUID отгрузки из 1С — ключ
    shipment_num: str | None = None     # номер документа отгрузки
    shipment_date: str | None = None    # ISO YYYY-MM-DD
    order_id: str                       # GUID заказа (линк)
    client_id: str
    client_name: str | None = None
    niche: str | None = None
    n3_id: str
    n4_id: str
    qty: float                          # фактически отгружено
    deleted: bool = False


class IngestShipmentLinesIn(BaseModel):
    items: list[IngestShipmentLine] = Field(default_factory=list)


class IngestShipmentResult(BaseModel):
    accepted: int
    shipments_affected: int | None = None


# --- Analyze (operator → us; endpoint 2) ---
class OrderLineIn(BaseModel):
    """A line of the current (draft) order. Analyzer works at N3 level: send
    ``n4_id`` (n3 resolved server-side) or ``n3_id`` directly."""

    n4_id: str | None = None
    n3_id: str | None = None
    qty: float = 0.0


class AnalyzeIn(BaseModel):
    client_id: str
    order_id: str | None = None   # заказ, по которому идёт анализ (для журнала рекомендаций)
    lines: list[OrderLineIn] = Field(default_factory=list)


class ForgottenItem(BaseModel):
    type: str
    n3_id: str
    n4_id: str | None = None
    order_count: int
    total_orders: int
    shipped_qty: float = 0.0        # Σ отгружено тонн клиентом по подгруппе (из РТУ)
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
    shipped_qty: float = 0.0        # Σ отгружено тонн в нише по подгруппе (из РТУ)
    frequency_pct: float
    client_bought_before: bool
    avg_qty_per_client: float
    priority: str


class AnalyzeOut(BaseModel):
    client_id: str
    niche: str | None = None
    analysis1_forgotten: list[ForgottenItem]
    analysis2_niche: list[NicheItem]


# --- Effect витрина (us → BI; read) ---
class EffectFunnelRow(BaseModel):
    """Строка витрины эффекта (грейн order_id × n3_id). Только GUID + меры."""

    order_id: str
    n3_id: str
    client_id: str | None = None
    niche: str | None = None
    period_month: str | None = None       # YYYY-MM-01 (месяц заказа)
    qty_original: float
    qty_final: float
    qty_added: float
    qty_shipped: float
    is_original: int
    is_final: int
    is_new_position: int
    is_qty_increased: int
    is_shipped: int


class EffectFunnelPage(BaseModel):
    items: list[EffectFunnelRow]
    limit: int
    offset: int
    count: int                            # всего строк по фильтру (для пагинации)


class RecommendationLogRow(BaseModel):
    id: int
    created_at: str | None = None
    client_id: str
    order_id: str | None = None
    kind: str                             # 'forgotten' | 'niche'
    n3_id: str
    n4_id: str | None = None
    priority: str | None = None


class RecommendationLogPage(BaseModel):
    items: list[RecommendationLogRow]
    limit: int
    offset: int
    count: int
