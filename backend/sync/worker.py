"""Background worker — rebuilds materialized aggregates on an interval.

    python -m backend.sync.worker

Push model: 1С ingests raw rows continuously via /api/ingest; this worker
rebuilds the analytical materialized views every ``REFRESH_INTERVAL_MINUTES``
(plus once on startup). Runs as its own container (see docker-compose.yml).
"""
from __future__ import annotations

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from backend.app.core.config import settings
from backend.app.db.session import engine
from backend.app.repository import refresh_aggregates

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("sync.worker")


async def _safe_refresh() -> None:
    try:
        await refresh_aggregates()
        logger.info("aggregates refreshed")
    except Exception:
        logger.exception("aggregate refresh failed; will retry next interval")


async def main() -> None:
    logger.info("worker starting (refresh every %dm)", settings.refresh_interval_minutes)
    await _safe_refresh()  # initial build on startup

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _safe_refresh,
        "interval",
        minutes=settings.refresh_interval_minutes,
        id="refresh_aggregates",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    try:
        await asyncio.Event().wait()  # keep the loop alive
    finally:
        scheduler.shutdown(wait=False)
        await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
