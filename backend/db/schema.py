"""Apply the database schema (idempotent) — replaces Alembic migrations.

    python -m backend.db.schema

Executes schema.sql (CREATE … IF NOT EXISTS). Run once on container start before
seeding. Schema changes are made by editing schema.sql and recreating the DB
volume (no incremental migrations).
"""
from __future__ import annotations

import asyncio
import pathlib
import re

from sqlalchemy import text

from backend.app.db.session import engine

_SCHEMA = pathlib.Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")


def _statements(sql: str) -> list[str]:
    # Strip ``--`` comments to end-of-line FIRST, then split on ``;`` — so a
    # semicolon inside a comment can't break statement splitting. (schema.sql has
    # no string literals containing ``--``.)
    no_comments = "\n".join(re.sub(r"--.*$", "", line) for line in sql.splitlines())
    return [s.strip() for s in no_comments.split(";") if s.strip()]


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
