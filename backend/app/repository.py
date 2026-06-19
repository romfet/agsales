"""Async data-access layer over PostgreSQL.

Replaces the old in-memory ``data_store``: same logical accessors, but every
aggregate is read (parameterized) from materialized views instead of being held
in a process-global pandas frame. Returns plain dicts/lists — no DataFrames.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import distinct, func, select, text

from backend.app.db.session import engine
from backend.app.db import tables as t


def _f(v: Any) -> float | None:
    """Decimal/None -> float/None for JSON-friendly output."""
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    return v


def _row_to_client_profile(r) -> dict:
    return {
        "n3": r.n3,
        "n4": r.n4,
        "item_guid": r.item_guid,
        "order_count": int(r.order_count),
        "total_orders": int(r.total_orders),
        "frequency_pct": _f(r.frequency_pct),
        "avg_qty_per_order": _f(r.avg_qty_per_order),
    }


def _row_to_niche_profile(r) -> dict:
    return {
        "n3": r.n3,
        "n4": r.n4,
        "item_guid": r.item_guid,
        "client_count": int(r.client_count),
        "total_clients_in_niche": int(r.total_clients_in_niche),
        "order_count": int(r.order_count),
        "total_orders_in_niche": int(r.total_orders_in_niche),
        "niche_pct": _f(r.niche_pct),
        "niche_freq_pct": _f(r.niche_freq_pct),
        "avg_qty_per_client": _f(r.avg_qty_per_client),
    }


# --- Lookups -------------------------------------------------------------

async def get_clients() -> list[dict]:
    q = select(
        t.client_dim.c.client_guid,
        t.client_dim.c.client_name,
        t.client_dim.c.niche,
    ).order_by(t.client_dim.c.client_name)
    async with engine.connect() as conn:
        rows = (await conn.execute(q)).all()
    return [
        {"client_guid": r.client_guid, "client_name": r.client_name, "niche": r.niche}
        for r in rows
    ]


async def get_client_niche(client_guid: str) -> str | None:
    q = select(t.client_dim.c.niche).where(t.client_dim.c.client_guid == client_guid)
    async with engine.connect() as conn:
        return (await conn.execute(q)).scalar_one_or_none()


async def get_client_total_orders(client_guid: str) -> int:
    q = select(t.client_dim.c.total_orders).where(
        t.client_dim.c.client_guid == client_guid
    )
    async with engine.connect() as conn:
        v = (await conn.execute(q)).scalar_one_or_none()
    return int(v) if v is not None else 0


async def get_products_n3() -> list[str]:
    q = select(distinct(t.products.c.n3)).order_by(t.products.c.n3)
    async with engine.connect() as conn:
        return [r[0] for r in (await conn.execute(q)).all()]


async def get_products_n4(n3: str) -> list[dict]:
    q = (
        select(t.products.c.n4, t.products.c.item_guid)
        .where(t.products.c.n3 == n3)
        .order_by(t.products.c.n4)
    )
    async with engine.connect() as conn:
        rows = (await conn.execute(q)).all()
    return [{"n4": r.n4, "item_guid": r.item_guid} for r in rows]


# --- Profiles ------------------------------------------------------------

async def get_client_profile_n4(client_guid: str) -> list[dict]:
    q = select(t.client_profile_n4).where(
        t.client_profile_n4.c.client_guid == client_guid
    )
    async with engine.connect() as conn:
        rows = (await conn.execute(q)).all()
    return [_row_to_client_profile(r) for r in rows]


async def get_niche_profile_n4(niche: str) -> list[dict]:
    q = select(t.niche_profile_n4).where(t.niche_profile_n4.c.niche == niche)
    async with engine.connect() as conn:
        rows = (await conn.execute(q)).all()
    return [_row_to_niche_profile(r) for r in rows]


async def get_client_bought_item_guids(client_guid: str) -> set[str]:
    q = select(distinct(t.client_profile_n4.c.item_guid)).where(
        t.client_profile_n4.c.client_guid == client_guid
    )
    async with engine.connect() as conn:
        return {r[0] for r in (await conn.execute(q)).all()}


# --- Orders --------------------------------------------------------------

async def get_order_lines(order_num: str) -> dict | None:
    """Look up an order by its (display) number. Returns {client, lines:[...]}."""
    ol = t.order_lines
    q = select(
        ol.c.client_name, ol.c.n3, ol.c.n4, ol.c.item_guid, ol.c.qty
    ).where(ol.c.order_num == order_num)
    async with engine.connect() as conn:
        rows = (await conn.execute(q)).all()
    if not rows:
        return None
    return {
        "client": rows[0].client_name,
        "lines": [
            {
                "n3": r.n3,
                "n4": r.n4,
                "item_guid": r.item_guid,
                "qty": _f(r.qty),
            }
            for r in rows
        ],
    }


async def search_orders(query: str, client_guid: str | None = None) -> list[dict]:
    """Substring search on order number; latest 10 by date."""
    ol = t.order_lines
    conds = [ol.c.order_num.ilike(f"%{query}%")]
    if client_guid:
        conds.append(ol.c.client_guid == client_guid)
    q = (
        select(
            ol.c.order_num,
            func.max(ol.c.order_date).label("date"),
            func.count(ol.c.n3).label("line_count"),
            func.min(ol.c.client_name).label("client"),
        )
        .where(*conds)
        .group_by(ol.c.order_num)
        .order_by(func.max(ol.c.order_date).desc())
        .limit(10)
    )
    async with engine.connect() as conn:
        rows = (await conn.execute(q)).all()
    return [
        {
            "order_num": r.order_num,
            "date": r.date.isoformat() if r.date else None,
            "line_count": int(r.line_count),
            "client": r.client,
        }
        for r in rows
    ]


# --- Stock ---------------------------------------------------------------

async def get_stock_for_n4(item_guid: str | None) -> dict | None:
    if not item_guid:
        return None
    q = select(t.stock.c.on_stock, t.stock.c.in_transit).where(
        t.stock.c.item_guid == item_guid
    )
    async with engine.connect() as conn:
        r = (await conn.execute(q)).one_or_none()
    if r is None:
        return None
    return {"on_stock": _f(r.on_stock), "in_transit": _f(r.in_transit)}


async def get_stock_map(item_guids: list[str]) -> dict[str, dict]:
    """Batch stock lookup, keyed by item_guid (avoids N+1 in the analyzer)."""
    if not item_guids:
        return {}
    q = select(t.stock.c.item_guid, t.stock.c.on_stock, t.stock.c.in_transit).where(
        t.stock.c.item_guid.in_(item_guids)
    )
    async with engine.connect() as conn:
        rows = (await conn.execute(q)).all()
    return {
        r.item_guid: {"on_stock": _f(r.on_stock), "in_transit": _f(r.in_transit)}
        for r in rows
    }


# --- Sync state ----------------------------------------------------------

async def get_sync_state() -> dict:
    q = select(t.sync_state).where(t.sync_state.c.id == 1)
    async with engine.connect() as conn:
        r = (await conn.execute(q)).one_or_none()
    if r is None:
        return {"status": "empty", "last_sync_at": None, "row_count": 0, "error": None}
    return {
        "status": r.status,
        "last_sync_at": r.last_sync_at.isoformat() if r.last_sync_at else None,
        "row_count": int(r.row_count),
        "error": r.error,
    }


async def refresh_aggregates() -> None:
    """Rebuild all materialized views, respecting dependency order.

    First refresh is non-concurrent (views may be unpopulated). Once populated,
    callers may switch to ``REFRESH MATERIALIZED VIEW CONCURRENTLY`` (unique
    indexes are already in place to allow it).
    """
    async with engine.begin() as conn:
        for mv in t.MATERIALIZED_VIEWS:
            await conn.execute(text(f"REFRESH MATERIALIZED VIEW {mv}"))
