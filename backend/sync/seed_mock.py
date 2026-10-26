"""Seed mock history for local dev, through the production ingest path.

    python -m backend.sync.seed_mock

Generates synthetic order lines and pushes them via the same repository ingest
used by the real 1С feed, then rebuilds aggregates. In INGEST_MODE=staged the
batches are merged right away (no worker wait). Used by the integration tests.
Dev-only.
"""
from __future__ import annotations

import asyncio

from backend.app import repository as repo
from backend.app import staging
from backend.app.core.config import settings
from backend.app.db.session import engine
from backend.sync.mock_source import generate_rows, orders_to_shipments


async def seed() -> int:
    rows = generate_rows()
    await repo.ingest_order_lines(rows)
    # Отгрузки — источник профилей рекомендаций (одна отгрузка на заказ, мок).
    await repo.ingest_shipment_lines(orders_to_shipments(rows))
    if settings.ingest_mode == "staged":
        for kind in staging.KINDS:
            await staging.merge_staging(kind, "manual")
    await repo.refresh_aggregates()
    return len(rows)


async def _main() -> None:
    n = await seed()
    print(f"Seeded {n} mock order lines (via ingest) and refreshed aggregates.")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
