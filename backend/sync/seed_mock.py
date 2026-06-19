"""Seed the database with mock data and refresh aggregates.

Usage (from repo root, with DB up and migrations applied):
    python -m backend.sync.seed_mock
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlalchemy import delete, insert, update

from backend.app.db import tables as t
from backend.app.db.session import engine
from backend.app.repository import refresh_aggregates
from backend.sync.mock_source import generate_rows, generate_stock


async def seed() -> int:
    rows = generate_rows()
    stock = generate_stock(rows)

    async with engine.begin() as conn:
        await conn.execute(delete(t.order_lines))
        await conn.execute(delete(t.stock))
        await conn.execute(insert(t.order_lines), rows)
        await conn.execute(insert(t.stock), stock)

    await refresh_aggregates()

    async with engine.begin() as conn:
        await conn.execute(
            update(t.sync_state)
            .where(t.sync_state.c.id == 1)
            .values(
                status="ok",
                last_sync_at=datetime.now(timezone.utc),
                row_count=len(rows),
                error=None,
            )
        )
    return len(rows)


async def _main() -> None:
    n = await seed()
    print(f"Seeded {n} mock order lines and refreshed aggregates.")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
