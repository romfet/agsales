"""Восстановление заказов из истории присылок (order_line_versions).

    python -m backend.sync.rebuild_orders_from_versions --dry-run
    python -m backend.sync.rebuild_orders_from_versions --backup-table order_line_versions_raw_20261026

До режима staged 1С присылала заказ по одному товару в разных запросах, и каждый
запрос заменял заказ целиком: в order_lines остался последний товар, а в
order_line_versions — «версия» на каждый товар. Скрипт собирает заказы обратно
(SDD_сборка_документов_при_приёме.md, §9.1):

1. Только заказы, которые сейчас есть в order_lines (удалённые не воскрешаем).
2. Сеансы выгрузки — по паузам > ``--gap-hours`` во всём потоке версий; цикл заказа —
   его версии внутри сеанса (повтор товара внутри сеанса — новый проход, новый цикл).
3. Состав цикла: по каждому товару — строки из последней версии цикла с ним. Из прошлых
   циклов ничего не переносится (удалённая позиция не воскресает). Ограничение: если
   первая присылка цикла совпала с последней прошлого (версия не записана дедупом),
   этот товар в цикле отсутствует.
4. Новая история: одна версия на цикл; order_lines = состав последнего цикла.

Запускать в окне обслуживания при остановленном приёме, после pg_dump; запись — только
с ``--confirm-backup``. Запись отказывается, если за время расчёта пришли данные. Старая
история копируется в ``--backup-table``; таблица order_line_versions переписывается на
месте (витрины эффекта ссылаются на неё по OID — переименовывать нельзя).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import text

from backend.app.db.session import engine
from backend.app.repository import _content_hash, refresh_aggregates

logger = logging.getLogger("sync.rebuild_orders")

_LINE_COLS = ("order_id", "order_num", "order_date", "client_id", "client_name",
              "niche", "n3_id", "n4_id", "qty")
_CHUNK = 5000
_MERGE_LOCK_KEY = 776611   # тот же ключ, что у сборки заказов (app/staging.py)


@dataclass
class Report:
    orders: int = 0
    orders_changed: int = 0
    products_added: int = 0
    versions_before: int = 0
    versions_after: int = 0
    orders_only_in_versions: int = 0
    sessions: int = 0
    n4_before: Counter = field(default_factory=Counter)
    n4_after: Counter = field(default_factory=Counter)

    def as_dict(self) -> dict:
        return {
            "orders": self.orders, "orders_changed": self.orders_changed,
            "products_added": self.products_added,
            "versions_before": self.versions_before, "versions_after": self.versions_after,
            "orders_only_in_versions": self.orders_only_in_versions,
            "sessions": self.sessions,
            "products_per_order_before": _buckets(self.n4_before),
            "products_per_order_after": _buckets(self.n4_after),
        }


def _buckets(c: Counter) -> dict[str, int]:
    """Распределение «товаров в заказе»: 1, 2, 3, 4, 5+."""
    out = {"1": 0, "2": 0, "3": 0, "4": 0, "5+": 0}
    for n, cnt in c.items():
        key = str(n) if n < 5 else "5+"
        out[key] = out.get(key, 0) + cnt
    return out


def rebuild_order(versions: list[dict], session_of: dict[int, int]) -> list[dict]:
    """Пересобрать историю одного заказа. ``versions`` — по возрастанию version_seq:
    ``{"seq", "at", "lines": [...]}``; ``session_of`` — version_seq → номер сеанса
    выгрузки (глобальные паузы в потоке, см. ``_sessions``). Цикл заказа = его версии
    внутри одного сеанса; повтор товара внутри сеанса начинает новый цикл (новый проход
    выгрузки). Состав цикла — по каждому товару строки из последней версии цикла с ним.
    Товары из прошлых циклов НЕ переносятся: позиция, удалённая между выгрузками,
    не воскресает. Возвращает новые версии (без подряд идущих одинаковых)."""
    cycles: list[list[dict]] = []
    seen: set[str] = set()
    for v in versions:
        n4s = {l["n4_id"] for l in v["lines"]}
        if (not cycles or session_of[v["seq"]] != session_of[cycles[-1][-1]["seq"]]
                or n4s & seen):
            cycles.append([])
            seen = set()
        cycles[-1].append(v)
        seen |= n4s

    out: list[dict] = []
    for cyc in cycles:
        comp: dict[str, list[dict]] = {}
        for v in cyc:
            by_n4: dict[str, list[dict]] = {}
            for l in v["lines"]:
                by_n4.setdefault(l["n4_id"], []).append(l)
            comp.update(by_n4)
        lines = [l for n4 in sorted(comp) for l in comp[n4]]
        h = _content_hash(lines)
        if not out or out[-1]["hash"] != h:
            out.append({"seq": max(v["seq"] for v in cyc), "at": cyc[0]["at"],
                        "hash": h, "lines": lines})
    return out


def sessions_from_gaps(seq_times: list[tuple[int, object]], gap: timedelta) -> dict[int, int]:
    """version_seq → номер сеанса: новый сеанс после паузы > gap во ВСЁМ потоке версий
    (ночная выгрузка идёт часами, но без пауз между пачками)."""
    out: dict[int, int] = {}
    sess, prev = 0, None
    for seq, at in seq_times:
        if prev is not None and at - prev > gap:
            sess += 1
        out[seq] = sess
        prev = at
    return out


async def _current_orders(conn) -> dict[str, tuple[str, int]]:
    """order_id → (хэш текущего состава, число товаров) по order_lines."""
    cur: dict[str, list[dict]] = {}
    res = await conn.stream(text(
        "SELECT order_id, n3_id, n4_id, qty FROM order_lines ORDER BY order_id"))
    async for r in res:
        cur.setdefault(r.order_id, []).append({"n3_id": r.n3_id, "n4_id": r.n4_id, "qty": r.qty})
    return {oid: (_content_hash(ls), len({l["n4_id"] for l in ls})) for oid, ls in cur.items()}


async def _iter_order_versions(conn):
    """(order_id, [версии]) по всем заказам из order_lines, по порядку."""
    res = await conn.stream(text(
        f"""
        SELECT v.version_seq, v.ingested_at, {', '.join('v.' + c for c in _LINE_COLS)}
        FROM order_line_versions v
        WHERE EXISTS (SELECT 1 FROM order_lines o WHERE o.order_id = v.order_id)
        ORDER BY v.order_id, v.version_seq, v.id
        """))
    oid, vers = None, []
    async for r in res:
        line = {c: getattr(r, c) for c in _LINE_COLS}
        if r.order_id != oid:
            if oid is not None:
                yield oid, vers
            oid, vers = r.order_id, []
        if not vers or vers[-1]["seq"] != r.version_seq:
            vers.append({"seq": r.version_seq, "at": r.ingested_at, "lines": []})
        vers[-1]["lines"].append(line)
    if oid is not None:
        yield oid, vers


async def rebuild(gap_hours: float = 2.0, dry_run: bool = True,
                  backup_table: str | None = None, refresh: bool = True,
                  confirm_backup: bool = False) -> dict:
    gap = timedelta(hours=gap_hours)
    rep = Report()
    if not dry_run:
        if not backup_table or not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", backup_table):
            raise ValueError("--backup-table обязателен для записи: имя [a-z_][a-z0-9_]*")
        if not confirm_backup:
            raise ValueError("запись только с --confirm-backup (после pg_dump: стенд не резервируется)")
        async with engine.begin() as conn:
            if (await conn.execute(text("SELECT to_regclass('_rebuild_versions')"))).scalar():
                raise RuntimeError("_rebuild_versions уже есть: идёт другой запуск или прошлый "
                                   "прерван — удалите _rebuild_versions и _rebuild_orders вручную")
            pending = (await conn.execute(text(
                "SELECT count(*) FROM ingest_batches WHERE kind = 'orders' AND merged_at IS NULL"
            ))).scalar()
            if pending:
                raise RuntimeError(f"есть несобранные пачки заказов ({pending}) — остановите приём")
            exists = (await conn.execute(text("SELECT to_regclass(:t)"), {"t": backup_table})).scalar()
            if exists:
                raise RuntimeError(f"таблица {backup_table} уже существует")
            await conn.execute(text(
                f"CREATE TABLE {backup_table} AS SELECT * FROM order_line_versions"))
            await conn.execute(text(
                "CREATE UNLOGGED TABLE _rebuild_versions AS SELECT version_seq, ingested_at, "
                "content_hash, " + ", ".join(_LINE_COLS) + " FROM order_line_versions WITH NO DATA"))
            await conn.execute(text("CREATE UNLOGGED TABLE IF NOT EXISTS _rebuild_orders (order_id text)"))
            await conn.execute(text("TRUNCATE _rebuild_orders"))

    async with engine.connect() as rconn:
        await rconn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
        # Метки снимка: при записи сверяем, что за время расчёта ничего не пришло.
        mark = (await rconn.execute(text(
            "SELECT (SELECT max(id) FROM order_line_versions) AS v, "
            "       (SELECT max(id) FROM order_lines) AS o"))).one()
        seq_times = (await rconn.execute(text(
            "SELECT version_seq, min(ingested_at) FROM order_line_versions "
            "GROUP BY version_seq ORDER BY version_seq"))).all()
        session_of = sessions_from_gaps([(r[0], r[1]) for r in seq_times], gap)
        rep.sessions = len(set(session_of.values()))
        current = await _current_orders(rconn)
        rep.orders_only_in_versions = (await rconn.execute(text(
            "SELECT count(DISTINCT order_id) FROM order_line_versions v "
            "WHERE NOT EXISTS (SELECT 1 FROM order_lines o WHERE o.order_id = v.order_id)"
        ))).scalar()

        buf_v: list[dict] = []
        buf_o: list[dict] = []

        async def flush() -> None:
            if dry_run or not (buf_v or buf_o):
                return
            async with engine.begin() as w:
                if buf_v:
                    await w.execute(text(
                        "INSERT INTO _rebuild_versions (version_seq, ingested_at, content_hash, "
                        + ", ".join(_LINE_COLS) + ") VALUES (:version_seq, :ingested_at, "
                        ":content_hash, " + ", ".join(":" + c for c in _LINE_COLS) + ")"), buf_v)
                if buf_o:
                    await w.execute(text("INSERT INTO _rebuild_orders VALUES (:order_id)"), buf_o)
            buf_v.clear()
            buf_o.clear()

        async for oid, vers in _iter_order_versions(rconn):
            rep.orders += 1
            rep.versions_before += len(vers)
            new = rebuild_order(vers, session_of)
            rep.versions_after += len(new)
            cur_hash, cur_n4 = current.get(oid, (None, 0))
            last = new[-1]
            new_n4 = len({l["n4_id"] for l in last["lines"]})
            rep.n4_before[cur_n4] += 1
            rep.n4_after[new_n4] += 1
            old_hashes = [_content_hash(v["lines"]) for v in vers]
            if last["hash"] != cur_hash:
                rep.orders_changed += 1
                rep.products_added += max(new_n4 - cur_n4, 0)
            if last["hash"] == cur_hash and [v["hash"] for v in new] == old_hashes:
                continue                                    # история уже корректна
            buf_o.append({"order_id": oid})
            buf_v.extend({**l, "version_seq": v["seq"], "ingested_at": v["at"],
                          "content_hash": v["hash"]} for v in new for l in v["lines"])
            if len(buf_v) >= _CHUNK:
                await flush()
        await flush()

    if not dry_run:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT pg_advisory_xact_lock(CAST(:a AS int), 1)"),
                               {"a": _MERGE_LOCK_KEY})
            # Приём direct не берёт advisory-lock: блокируем запись в таблицы заказов и
            # отказываемся, если после снимка что-то пришло.
            await conn.execute(text(
                "LOCK TABLE order_lines, order_line_versions IN SHARE ROW EXCLUSIVE MODE"))
            now_mark = (await conn.execute(text(
                "SELECT (SELECT max(id) FROM order_line_versions) AS v, "
                "       (SELECT max(id) FROM order_lines) AS o"))).one()
            if (now_mark.v, now_mark.o) != (mark.v, mark.o):
                raise RuntimeError("за время расчёта пришли новые данные заказов — "
                                   "остановите приём и запустите снова "
                                   "(удалите _rebuild_versions и _rebuild_orders)")
            await conn.execute(text(
                "DELETE FROM order_line_versions v USING _rebuild_orders r "
                "WHERE v.order_id = r.order_id"))
            await conn.execute(text(
                "INSERT INTO order_line_versions (version_seq, ingested_at, content_hash, "
                + ", ".join(_LINE_COLS) + ") SELECT version_seq, ingested_at, content_hash, "
                + ", ".join(_LINE_COLS) + " FROM _rebuild_versions"))
            # order_lines = состав последней версии заказа.
            await conn.execute(text(
                "DELETE FROM order_lines o USING _rebuild_orders r WHERE o.order_id = r.order_id"))
            await conn.execute(text(
                f"""
                INSERT INTO order_lines ({', '.join(_LINE_COLS)})
                SELECT {', '.join('v.' + c for c in _LINE_COLS)} FROM _rebuild_versions v
                JOIN (SELECT order_id, max(version_seq) AS s FROM _rebuild_versions GROUP BY 1) m
                  ON m.order_id = v.order_id AND m.s = v.version_seq
                """))
            await conn.execute(text("DROP TABLE _rebuild_versions"))
            await conn.execute(text("DROP TABLE _rebuild_orders"))
        if refresh:
            await refresh_aggregates()
    return rep.as_dict()


async def _main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="только отчёт, без записи")
    ap.add_argument("--gap-hours", type=float, default=2.0, help="пауза, начинающая новый цикл")
    ap.add_argument("--backup-table", help="куда скопировать старую историю (обязательно для записи)")
    ap.add_argument("--no-refresh", action="store_true", help="не пересчитывать агрегаты")
    ap.add_argument("--confirm-backup", action="store_true",
                    help="подтверждаю: pg_dump сделан, приём остановлен (обязательно для записи)")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    rep = await rebuild(a.gap_hours, a.dry_run, a.backup_table, not a.no_refresh, a.confirm_backup)
    for k, v in rep.items():
        print(f"{k}: {v}")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
