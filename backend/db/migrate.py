"""Apply database migrations in order (lightweight SQL runner).

    python -m backend.db.migrate

Каждый ``db/migrations/NNNN_*.sql`` применяется один раз (в порядке имён) и
отмечается в таблице ``schema_migrations``. ``0001_baseline.sql`` — полная схема
(идемпотентные ``CREATE … IF NOT EXISTS``), поэтому безопасна и на уже наполненном
стенде: объекты не пересоздаются, версия просто отмечается применённой.

Изменение схемы = **добавить новый** ``NNNN_*.sql`` (ALTER/CREATE). НЕ править уже
применённую миграцию и НЕ пересоздавать том БД — реальные данные 1С должны выжить
между деплоями (никакого ``docker compose down -v`` в обычном цикле).
"""
from __future__ import annotations

import asyncio
import pathlib
import re

from sqlalchemy import text

from backend.app.db.session import engine

_MIGRATIONS = pathlib.Path(__file__).with_name("migrations")


def _statements(sql: str) -> list[str]:
    # Убираем ``--``-комментарии до конца строки, ПОТОМ бьём на ``;`` — чтобы точка
    # с запятой внутри комментария не ломала разбиение (строковых литералов с ``--``
    # в миграциях нет).
    no_comments = "\n".join(re.sub(r"--.*$", "", line) for line in sql.splitlines())
    return [s.strip() for s in no_comments.split(";") if s.strip()]


async def _applied() -> set[str]:
    async with engine.begin() as conn:
        await conn.execute(text(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version text PRIMARY KEY,"
            " applied_at timestamptz NOT NULL DEFAULT now())"
        ))
        rows = (await conn.execute(text("SELECT version FROM schema_migrations"))).all()
    return {r[0] for r in rows}


async def migrate() -> list[str]:
    """Применить все ещё не применённые миграции по порядку. Возвращает список
    только что применённых версий. Каждая миграция — в своей транзакции."""
    files = sorted(p for p in _MIGRATIONS.glob("*.sql"))
    done = await _applied()
    newly: list[str] = []
    for f in files:
        version = f.name
        if version in done:
            continue
        statements = _statements(f.read_text(encoding="utf-8"))
        async with engine.begin() as conn:
            for stmt in statements:
                await conn.execute(text(stmt))
            await conn.execute(
                text("INSERT INTO schema_migrations (version) VALUES (:v)"),
                {"v": version},
            )
        newly.append(version)
    return newly


async def _main() -> None:
    newly = await migrate()
    print(f"migrations applied: {newly}" if newly else "migrations: up to date")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
