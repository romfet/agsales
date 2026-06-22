"""Async data-access layer over PostgreSQL.

Two responsibilities in the slimmed (two-endpoint) service:
- **Ingest writes** — upsert the pushed 1С feed into the raw tables.
- **Analyze reads** — read per-client/per-niche aggregates from materialized
  views (never raw rows), plus resolve item_guid → n3 for the draft order.

Aggregates are rebuilt by ``refresh_aggregates`` (the worker, on an interval).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, distinct, insert, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from backend.app.db import tables as t
from backend.app.db.session import engine


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


# --- Ingest (1С feed → raw tables) ---------------------------------------

_ORDER_FIELDS = ("order_guid", "order_num", "order_date", "client_guid",
                 "client_name", "niche", "n1", "n2", "n3", "n4", "item_guid", "qty")


def _order_row(it: dict) -> dict:
    row = {k: it.get(k) for k in _ORDER_FIELDS}
    od = row.get("order_date")
    if isinstance(od, str) and od:
        row["order_date"] = date.fromisoformat(od)  # ISO from the 1С feed
    elif isinstance(od, date):
        row["order_date"] = od  # date object from the mock seeder
    else:
        row["order_date"] = None
    return row


async def ingest_order_lines(items: list[dict]) -> dict:
    """Upsert pushed order lines. Grain is the document: every order_guid in the
    batch is replaced (delete-insert), so adding/changing/removing lines and
    full-order deletion (``deleted``) are all handled. Live aggregates are
    rebuilt separately by the worker."""
    by_order: dict[str, list[dict]] = {}
    deleted: set[str] = set()
    for it in items:
        og = it["order_guid"]
        if it.get("deleted"):
            deleted.add(og)
            by_order.pop(og, None)
            continue
        by_order.setdefault(og, []).append(it)

    affected = set(by_order) | deleted
    rows = [_order_row(it) for lines in by_order.values() for it in lines]

    async with engine.begin() as conn:
        if affected:
            await conn.execute(
                delete(t.order_lines).where(t.order_lines.c.order_guid.in_(affected))
            )
        if rows:
            await conn.execute(insert(t.order_lines), rows)
    return {"accepted": len(rows), "orders_affected": len(affected)}


async def ingest_stock(items: list[dict]) -> dict:
    """Upsert stock by item_guid (1С sends only changed rows)."""
    if not items:
        return {"accepted": 0}
    rows = [
        {
            "item_guid": it["item_guid"],
            "n4_name": it.get("n4_name"),
            "on_stock": it.get("on_stock"),
            "in_transit": it.get("in_transit"),
        }
        for it in items
    ]
    stmt = pg_insert(t.stock).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["item_guid"],
        set_={
            "n4_name": stmt.excluded.n4_name,
            "on_stock": stmt.excluded.on_stock,
            "in_transit": stmt.excluded.in_transit,
        },
    )
    async with engine.begin() as conn:
        await conn.execute(stmt)
    return {"accepted": len(rows)}


# --- Analyze reads --------------------------------------------------------

async def get_client_niche(client_guid: str) -> str | None:
    q = select(t.client_dim.c.niche).where(t.client_dim.c.client_guid == client_guid)
    async with engine.connect() as conn:
        return (await conn.execute(q)).scalar_one_or_none()


async def get_n3_for_items(item_guids: list[str]) -> dict[str, str]:
    """Resolve item_guid → n3 (subgroup) from the synced catalog — the analyzer
    matches the draft order at N3 level."""
    if not item_guids:
        return {}
    q = select(t.products.c.item_guid, t.products.c.n3).where(
        t.products.c.item_guid.in_(item_guids)
    )
    async with engine.connect() as conn:
        rows = (await conn.execute(q)).all()
    return {r.item_guid: r.n3 for r in rows}


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


# --- Aggregates -----------------------------------------------------------

async def refresh_aggregates() -> None:
    """Rebuild all materialized views, respecting dependency order.

    Already-populated views are refreshed CONCURRENTLY so they stay readable to
    the API during the rebuild (their unique indexes, created in migration 0001,
    make this possible); the first, unpopulated build falls back to a plain
    REFRESH. CONCURRENTLY cannot run inside a transaction block, so this uses an
    AUTOCOMMIT connection (one committed statement per view).
    """
    ac_engine = engine.execution_options(isolation_level="AUTOCOMMIT")
    async with ac_engine.connect() as conn:
        for mv in t.MATERIALIZED_VIEWS:
            populated = await conn.scalar(
                text("SELECT ispopulated FROM pg_matviews WHERE matviewname = :n"),
                {"n": mv},
            )
            mode = "CONCURRENTLY " if populated else ""
            await conn.execute(text(f"REFRESH MATERIALIZED VIEW {mode}{mv}"))
