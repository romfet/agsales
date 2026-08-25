"""Async data-access layer over PostgreSQL.

Ingest writes the pushed 1С feed (delta, upsert by order_num) into order_lines;
analyze reads per-client/per-niche aggregates from materialized views. Identity
is by GUID (client_id / n3_id / n4_id); names are display-only.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, insert, select, text

from backend.app.db import tables as t
from backend.app.db.session import engine


def _f(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    return v


def _row_to_client_profile_n3(r) -> dict:
    # rep_n4_id → n4_id: подгруппа несёт представительный товар (для отображения).
    return {
        "n3_id": r.n3_id,
        "n4_id": r.rep_n4_id,
        "order_count": int(r.order_count),
        "total_orders": int(r.total_orders),
        "frequency_pct": _f(r.frequency_pct),
        "avg_qty_per_order": _f(r.avg_qty_per_order),
    }


def _row_to_niche_profile_n3(r) -> dict:
    return {
        "n3_id": r.n3_id,
        "n4_id": r.rep_n4_id,
        "client_count": int(r.client_count),
        "total_clients_in_niche": int(r.total_clients_in_niche),
        "order_count": int(r.order_count),
        "total_orders_in_niche": int(r.total_orders_in_niche),
        "niche_pct": _f(r.niche_pct),
        "niche_freq_pct": _f(r.niche_freq_pct),
        "avg_qty_per_client": _f(r.avg_qty_per_client),
    }


# --- Ingest (1С feed → raw table) ----------------------------------------

_ORDER_FIELDS = ("order_id", "order_num", "order_date", "client_id", "client_name",
                 "niche", "n3_id", "n4_id", "qty")


def _to_date(v) -> date | None:
    """Дата из ISO-строки или datetime, устойчиво к времени: '2025-01-01T08:00'
    и '2025-01-01' → date. (1С шлёт даты и с временем — не роняем ingest.)"""
    if v is None or v == "":
        return None
    if isinstance(v, datetime):     # datetime — подкласс date, проверяем первым
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        return date.fromisoformat(v[:10])
    return None


def _content_hash(lines: list[dict]) -> str:
    """Хэш состава заказа (набор строк n3×n4×qty) — для дедупа неизменных версий."""
    parts = sorted(
        f"{l.get('n3_id')}|{l.get('n4_id')}|{float(l.get('qty') or 0):.3f}" for l in lines
    )
    return hashlib.md5("\n".join(parts).encode()).hexdigest()


def _order_row(it: dict) -> dict:
    row = {k: it.get(k) for k in _ORDER_FIELDS}
    row["order_date"] = _to_date(row.get("order_date"))
    return row


async def ingest_order_lines(items: list[dict]) -> dict:
    """Upsert pushed order lines. Grain is the document, keyed by ``order_id``
    (GUID из 1С): every order_id in the batch is replaced (delete-insert);
    ``deleted`` removes it. order_lines хранит текущий срез; order_line_versions —
    историю версий (для диффа исходное→финал)."""
    by_order: dict[str, list[dict]] = {}
    deleted: set[str] = set()
    for it in items:
        oid = it["order_id"]
        if it.get("deleted"):
            deleted.add(oid)
            by_order.pop(oid, None)
            continue
        by_order.setdefault(oid, []).append(it)

    affected = set(by_order) | deleted
    rows = [_order_row(it) for lines in by_order.values() for it in lines]
    hash_by_order = {oid: _content_hash(lines) for oid, lines in by_order.items()}

    async with engine.begin() as conn:
        if affected:
            await conn.execute(
                delete(t.order_lines).where(t.order_lines.c.order_id.in_(affected))
            )
        if rows:
            await conn.execute(insert(t.order_lines), rows)
        # Версии: пишем снимок только для заказов, чей СОСТАВ изменился с прошлой
        # версии (дедуп неизменных). Дискриминатор — монотонный version_seq (один
        # на всю пачку), не таймстемп.
        if by_order:
            prev = (await conn.execute(
                select(t.order_line_versions.c.order_id, t.order_line_versions.c.content_hash)
                .where(t.order_line_versions.c.order_id.in_(by_order))
                .distinct(t.order_line_versions.c.order_id)
                .order_by(
                    t.order_line_versions.c.order_id,
                    t.order_line_versions.c.version_seq.desc(),
                )
            )).all()
            last_hash = {r.order_id: r.content_hash for r in prev}
            changed = [oid for oid in by_order if hash_by_order[oid] != last_hash.get(oid)]
            if changed:
                seq = await conn.scalar(text("SELECT nextval('order_version_seq')"))
                version_rows = [
                    {**_order_row(it), "version_seq": seq, "content_hash": hash_by_order[oid]}
                    for oid in changed
                    for it in by_order[oid]
                ]
                await conn.execute(insert(t.order_line_versions), version_rows)
    return {"accepted": len(rows), "orders_affected": len(affected)}


# --- Ingest (отгрузки: факт отгруженного) --------------------------------

_SHIPMENT_FIELDS = ("shipment_id", "shipment_num", "shipment_date", "order_id",
                    "client_id", "client_name", "niche", "n3_id", "n4_id", "qty")


def _shipment_row(it: dict) -> dict:
    row = {k: it.get(k) for k in _SHIPMENT_FIELDS}
    row["shipment_date"] = _to_date(row.get("shipment_date"))
    return row


async def ingest_shipment_lines(items: list[dict]) -> dict:
    """Upsert строк отгрузки. Ключ — shipment_id (GUID из 1С): каждый shipment_id
    в пачке заменяется целиком (delete-insert), ``deleted`` удаляет. Отгрузка —
    факт, версий не держим. Заказ↔отгрузка — m2m через order_id на строке."""
    by_ship: dict[str, list[dict]] = {}
    deleted: set[str] = set()
    for it in items:
        sid = it["shipment_id"]
        if it.get("deleted"):
            deleted.add(sid)
            by_ship.pop(sid, None)
            continue
        by_ship.setdefault(sid, []).append(it)

    affected = set(by_ship) | deleted
    rows = [_shipment_row(it) for lines in by_ship.values() for it in lines]

    async with engine.begin() as conn:
        if affected:
            await conn.execute(
                delete(t.shipment_lines).where(t.shipment_lines.c.shipment_id.in_(affected))
            )
        if rows:
            await conn.execute(insert(t.shipment_lines), rows)
    return {"accepted": len(rows), "shipments_affected": len(affected)}


# --- Recommendation log (сигнал «ассистент сработал» + что рекомендовали) --

async def log_recommendations(
    client_id: str, order_id: str | None,
    forgotten: list[dict], niche_recs: list[dict],
) -> None:
    """Записать выданные рекомендации (по строке на позицию) в recommendation_log.
    created_at проставляет БД. Пусто — не пишем."""
    rows = [
        {"client_id": client_id, "order_id": order_id, "kind": "forgotten",
         "n3_id": r["n3_id"], "n4_id": r.get("n4_id"), "priority": r.get("priority")}
        for r in forgotten
    ] + [
        {"client_id": client_id, "order_id": order_id, "kind": "niche",
         "n3_id": r["n3_id"], "n4_id": r.get("n4_id"), "priority": r.get("priority")}
        for r in niche_recs
    ]
    if rows:
        async with engine.begin() as conn:
            await conn.execute(insert(t.recommendation_log), rows)


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


# --- Analyze reads at N3 grain (subgroup) — source of recommendations -----

async def get_client_profile_n3(client_id: str) -> list[dict]:
    q = select(t.client_profile_n3).where(t.client_profile_n3.c.client_id == client_id)
    async with engine.connect() as conn:
        rows = (await conn.execute(q)).all()
    return [_row_to_client_profile_n3(r) for r in rows]


async def get_niche_profile_n3(niche: str) -> list[dict]:
    q = select(t.niche_profile_n3).where(t.niche_profile_n3.c.niche == niche)
    async with engine.connect() as conn:
        rows = (await conn.execute(q)).all()
    return [_row_to_niche_profile_n3(r) for r in rows]


# --- Order versions / diff (для аналитики эффекта) ------------------------

async def get_order_diff(order_id: str) -> list[dict]:
    """Дифф заказа: первая версия (исходное) vs последняя (финал) по (n3_id, n4_id).
    status: added / removed / increased / decreased / unchanged. Основа воронки."""
    sql = text(
        """
        WITH bounds AS (
            SELECT min(version_seq) AS first_seq, max(version_seq) AS last_seq
            FROM order_line_versions WHERE order_id = :oid
        ),
        first_v AS (
            SELECT n3_id, n4_id, sum(qty) AS qty
            FROM order_line_versions v, bounds b
            WHERE v.order_id = :oid AND v.version_seq = b.first_seq
            GROUP BY n3_id, n4_id
        ),
        last_v AS (
            SELECT n3_id, n4_id, sum(qty) AS qty
            FROM order_line_versions v, bounds b
            WHERE v.order_id = :oid AND v.version_seq = b.last_seq
            GROUP BY n3_id, n4_id
        )
        SELECT n3_id, n4_id,
               COALESCE(f.qty, 0) AS qty_first,
               COALESCE(l.qty, 0) AS qty_last
        FROM first_v f FULL OUTER JOIN last_v l USING (n3_id, n4_id)
        ORDER BY n3_id, n4_id
        """
    )
    async with engine.connect() as conn:
        rows = (await conn.execute(sql, {"oid": order_id})).all()
    out: list[dict] = []
    for r in rows:
        qf, ql = float(r.qty_first), float(r.qty_last)
        if qf == 0 and ql > 0:
            status = "added"
        elif ql == 0 and qf > 0:
            status = "removed"
        elif ql > qf:
            status = "increased"
        elif ql < qf:
            status = "decreased"
        else:
            status = "unchanged"
        out.append({"n3_id": r.n3_id, "n4_id": r.n4_id,
                    "qty_first": qf, "qty_last": ql,
                    "delta": round(ql - qf, 3), "status": status})
    return out


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
