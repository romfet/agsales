"""Async data-access layer over PostgreSQL.

Ingest writes the pushed 1С feed (delta, upsert by order_num) into order_lines;
analyze reads per-client/per-niche aggregates from materialized views. Identity
is by GUID (client_id / n3_id / n4_id); names are display-only.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, distinct, insert, select, text

from backend.app.db import tables as t
from backend.app.db.session import engine


def _f(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    return v


def _row_to_client_profile(r) -> dict:
    return {
        "n3_id": r.n3_id,
        "n4_id": r.n4_id,
        "order_count": int(r.order_count),
        "total_orders": int(r.total_orders),
        "frequency_pct": _f(r.frequency_pct),
        "avg_qty_per_order": _f(r.avg_qty_per_order),
    }


def _row_to_niche_profile(r) -> dict:
    return {
        "n3_id": r.n3_id,
        "n4_id": r.n4_id,
        "client_count": int(r.client_count),
        "total_clients_in_niche": int(r.total_clients_in_niche),
        "order_count": int(r.order_count),
        "total_orders_in_niche": int(r.total_orders_in_niche),
        "niche_pct": _f(r.niche_pct),
        "niche_freq_pct": _f(r.niche_freq_pct),
        "avg_qty_per_client": _f(r.avg_qty_per_client),
    }


# --- Ingest (1С feed → raw table) ----------------------------------------

_ORDER_FIELDS = ("order_num", "order_date", "client_id", "client_name", "niche",
                 "n3_id", "n4_id", "qty")


def _order_row(it: dict) -> dict:
    row = {k: it.get(k) for k in _ORDER_FIELDS}
    od = row.get("order_date")
    if isinstance(od, str) and od:
        row["order_date"] = date.fromisoformat(od)
    elif isinstance(od, date):
        row["order_date"] = od
    else:
        row["order_date"] = None
    return row


async def ingest_order_lines(items: list[dict]) -> dict:
    """Upsert pushed order lines. Grain is the document (order_num): every
    order_num in the batch is replaced (delete-insert); ``deleted`` removes it."""
    by_order: dict[str, list[dict]] = {}
    deleted: set[str] = set()
    for it in items:
        on = it["order_num"]
        if it.get("deleted"):
            deleted.add(on)
            by_order.pop(on, None)
            continue
        by_order.setdefault(on, []).append(it)

    affected = set(by_order) | deleted
    rows = [_order_row(it) for lines in by_order.values() for it in lines]

    async with engine.begin() as conn:
        if affected:
            await conn.execute(
                delete(t.order_lines).where(t.order_lines.c.order_num.in_(affected))
            )
        if rows:
            await conn.execute(insert(t.order_lines), rows)
    return {"accepted": len(rows), "orders_affected": len(affected)}


# --- Analyze reads --------------------------------------------------------

async def get_client_niche(client_id: str) -> str | None:
    q = select(t.client_dim.c.niche).where(t.client_dim.c.client_id == client_id)
    async with engine.connect() as conn:
        return (await conn.execute(q)).scalar_one_or_none()


async def get_n3_for_n4(n4_ids: list[str]) -> dict[str, str]:
    """Resolve n4_id → n3_id from the synced catalog (analyzer matches at N3)."""
    if not n4_ids:
        return {}
    q = select(t.products.c.n4_id, t.products.c.n3_id).where(
        t.products.c.n4_id.in_(n4_ids)
    )
    async with engine.connect() as conn:
        rows = (await conn.execute(q)).all()
    return {r.n4_id: r.n3_id for r in rows}


async def get_client_profile_n4(client_id: str) -> list[dict]:
    q = select(t.client_profile_n4).where(t.client_profile_n4.c.client_id == client_id)
    async with engine.connect() as conn:
        rows = (await conn.execute(q)).all()
    return [_row_to_client_profile(r) for r in rows]


async def get_niche_profile_n4(niche: str) -> list[dict]:
    q = select(t.niche_profile_n4).where(t.niche_profile_n4.c.niche == niche)
    async with engine.connect() as conn:
        rows = (await conn.execute(q)).all()
    return [_row_to_niche_profile(r) for r in rows]


async def get_client_bought_n4_ids(client_id: str) -> set[str]:
    q = select(distinct(t.client_profile_n4.c.n4_id)).where(
        t.client_profile_n4.c.client_id == client_id
    )
    async with engine.connect() as conn:
        return {r[0] for r in (await conn.execute(q)).all()}


# --- Aggregates -----------------------------------------------------------

async def count_order_lines() -> int:
    async with engine.connect() as conn:
        return int(await conn.scalar(text("SELECT count(*) FROM order_lines")))


async def refresh_aggregates() -> None:
    """Rebuild all materialized views in dependency order (CONCURRENTLY once
    populated; AUTOCOMMIT since CONCURRENTLY can't run in a transaction)."""
    ac_engine = engine.execution_options(isolation_level="AUTOCOMMIT")
    async with ac_engine.connect() as conn:
        for mv in t.MATERIALIZED_VIEWS:
            populated = await conn.scalar(
                text("SELECT ispopulated FROM pg_matviews WHERE matviewname = :n"),
                {"n": mv},
            )
            mode = "CONCURRENTLY " if populated else ""
            await conn.execute(text(f"REFRESH MATERIALIZED VIEW {mode}{mv}"))
