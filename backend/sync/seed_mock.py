"""Seed mock history for local dev, through the production ingest path.

    python -m backend.sync.seed_mock

Generates synthetic order lines and pushes them via the same repository ingest
used by the real 1С feed, then rebuilds aggregates. Used by the ``migrate``
container and the integration tests. Dev-only.
"""
from __future__ import annotations

import asyncio

from backend.app import repository as repo
from backend.app.db.session import engine
from backend.sync.mock_source import generate_rows


async def seed() -> int:
    rows = generate_rows()
    await repo.ingest_order_lines(rows)
    await repo.refresh_aggregates()
    return len(rows)


async def _main() -> None:
    n = await seed()
    print(f"Seeded {n} mock order lines (via ingest) and refreshed aggregates.")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
