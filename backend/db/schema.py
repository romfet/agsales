"""Apply the database schema (idempotent) — replaces Alembic migrations.

    python -m backend.db.schema

Executes schema.sql (CREATE … IF NOT EXISTS). Run once on container start before
seeding. Schema changes are made by editing schema.sql and recreating the DB
volume (no incremental migrations).
"""
from __future__ import annotations

import asyncio
import pathlib

from sqlalchemy import text

from backend.app.db.session import engine

_SCHEMA = pathlib.Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")


def _statements(sql: str) -> list[str]:
    out = []
    for chunk in sql.split(";"):
        body = "\n".join(l for l in chunk.splitlines() if not l.strip().startswith("--")).strip()
        if body:
            out.append(chunk.strip())
    return out


async def apply_schema() -> None:
    async with engine.begin() as conn:
        for stmt in _statements(_SCHEMA):
            await conn.execute(text(stmt))


async def _main() -> None:
    await apply_schema()
    print("schema applied")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
