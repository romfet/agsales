"""Async data-access layer over PostgreSQL.

Ingest writes the pushed 1С feed into order_lines / shipment_lines — directly
(INGEST_MODE=direct: each request replaces its documents) or via staging and a
worker merge (INGEST_MODE=staged, see app/staging.py); analyze reads per-client/per-niche aggregates from materialized views. Identity
is by GUID (client_id / n3_id / n4_id); names are display-only.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, insert, select, text

from backend.app.core.config import settings
from backend.app.db import tables as t
from backend.app.db.session import engine

logger = logging.getLogger("ingest")


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
        "order_count": int(r.order_count),          # заказов клиента с подгруппой
        "total_orders": int(r.total_orders),        # всего заказов клиента
        "shipment_count": int(r.shipment_count),    # отгрузок (РТУ) с подгруппой
        "total_shipments": int(r.total_shipments),  # всего отгрузок клиента
        "frequency_pct": _f(r.frequency_pct),
        "avg_qty_per_order": _f(r.avg_qty_per_order),
        "total_qty_12m": _f(r.total_qty_12m),   # Σ отгружено тонн за 12 мес (из РТУ)
    }


def _row_to_niche_profile_n3(r) -> dict:
    return {
        "n3_id": r.n3_id,
        "n4_id": r.rep_n4_id,
        "client_count": int(r.client_count),
        "total_clients_in_niche": int(r.total_clients_in_niche),
        "order_count": int(r.order_count),                       # заказов в нише с подгруппой
        "total_orders_in_niche": int(r.total_orders_in_niche),   # всего заказов в нише
        "shipment_count": int(r.shipment_count),                 # отгрузок в нише с подгруппой
        "total_shipments_in_niche": int(r.total_shipments_in_niche),  # всего отгрузок в нише
        "niche_pct": _f(r.niche_pct),
        "niche_freq_pct": _f(r.niche_freq_pct),
        "avg_qty_per_client": _f(r.avg_qty_per_client),
        "total_qty_12m": _f(r.total_qty_12m),   # Σ отгружено тонн в нише за 12 мес (из РТУ)
    }


def _row_to_client_profile_n4(r) -> dict:
    return {
        "n3_id": r.n3_id,
        "n4_id": r.n4_id,
        "order_count": int(r.order_count),
        "total_orders": int(r.total_orders),
        "shipment_count": int(r.shipment_count),
        "total_shipments": int(r.total_shipments),
        "frequency_pct": _f(r.frequency_pct),
        "avg_qty_per_order": _f(r.avg_qty_per_order),
        "total_qty_12m": _f(r.total_qty_12m),
    }


def _row_to_niche_profile_n4(r) -> dict:
    return {
        "n3_id": r.n3_id,
        "n4_id": r.n4_id,
        "client_count": int(r.client_count),
        "total_clients_in_niche": int(r.total_clients_in_niche),
        "order_count": int(r.order_count),
        "total_orders_in_niche": int(r.total_orders_in_niche),
        "shipment_count": int(r.shipment_count),
        "total_shipments_in_niche": int(r.total_shipments_in_niche),
        "niche_pct": _f(r.niche_pct),
        "niche_freq_pct": _f(r.niche_freq_pct),
        "avg_qty_per_client": _f(r.avg_qty_per_client),
        "total_qty_12m": _f(r.total_qty_12m),
    }


# --- Ingest (1С feed → raw table) ----------------------------------------

_ORDER_FIELDS = ("order_id", "order_num", "order_date", "client_id", "client_name",
                 "niche", "n3_id", "n4_id", "qty")


# Не-ISO форматы дат, которые может прислать 1С (после провала ISO).
_DATE_FORMATS = ("%d.%m.%Y", "%d/%m/%Y", "%Y/%m/%d", "%d.%m.%y")


def _to_date(v) -> date | None:
    """Дата из datetime / ISO-строки / распространённых не-ISO форматов, устойчиво:
    '2025-01-01T08:00', '2025-01-01', '01.01.2025', '2025/01/01' → date.
    Непарсируемое значение → None (НЕ роняем всю пачку ingest из-за одной даты)."""
    if v is None or v == "":
        return None
    if isinstance(v, datetime):     # datetime — подкласс date, проверяем первым
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        s = v.strip()
        try:
            return date.fromisoformat(s[:10])   # ISO (в т.ч. с временем)
        except ValueError:
            for fmt in _DATE_FORMATS:
                try:
                    return datetime.strptime(s, fmt).date()
                except ValueError:
                    continue
            return None
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


async def _shrunk_documents(conn, table, key: str, new_n4: dict[str, set[str]]) -> list[str]:
    """Документы пачки, у которых в БД есть товары, отсутствующие в пачке: замена
    целиком их сузит (признак того, что 1С прислала документ не полностью)."""
    if not new_n4:
        return []
    col = table.c[key]
    rows = (await conn.execute(
        select(col, table.c.n4_id).where(col.in_(new_n4)).distinct()
    )).all()
    return sorted({r[0] for r in rows if r[1] not in new_n4[r[0]]})


async def _record_direct(conn, kind: str, items: int, by_doc: dict[str, list[dict]],
                         affected: set[str], shrunk: list[str]) -> None:
    """Итоги пачки режима direct → ingest_direct_log (видны в /api/ingest/stats) и
    предупреждение в лог, если документы сузились."""
    if shrunk:
        logger.warning(
            "ingest %s: %d document(s) replaced by a version with fewer products "
            "(1С sent a document in parts?), e.g. %s", kind, len(shrunk), shrunk[:3])
    if not items:
        return
    single = sum(1 for lines in by_doc.values() if len({l["n4_id"] for l in lines}) == 1)
    await conn.execute(text(
        "INSERT INTO ingest_direct_log (kind, items, documents, documents_shrunk, "
        "single_product_docs, shrunk_sample) VALUES (:k, :i, :d, :s, :one, :smp)"),
        {"k": kind, "i": items, "d": len(affected), "s": len(shrunk), "one": single,
         "smp": shrunk[0] if shrunk else None})


def _staged_row(row: dict, it: dict) -> dict:
    return {**row, "deleted": bool(it.get("deleted"))}


async def ingest_order_lines(items: list[dict]) -> dict:
    """Upsert pushed order lines. Grain is the document, keyed by ``order_id``
    (GUID из 1С): every order_id in the batch is replaced (delete-insert);
    ``deleted`` removes it. order_lines хранит текущий срез; order_line_versions —
    историю версий (для диффа исходное→финал).

    В режиме staged пачка только складывается в staging (app/staging.py)."""
    if settings.ingest_mode == "staged":
        from backend.app import staging
        return await staging.stage_batch(
            "orders", [_staged_row(_order_row(it), it) for it in items])

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
        shrunk = await _shrunk_documents(
            conn, t.order_lines, "order_id",
            {oid: {l["n4_id"] for l in lines} for oid, lines in by_order.items()})
        await _record_direct(conn, "orders", len(items), by_order, affected, shrunk)
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
    return {"accepted": len(rows), "orders_affected": len(affected),
            "status": "applied" if items else "empty", "documents_shrunk": len(shrunk)}


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
    факт, версий не держим. Заказ↔отгрузка — m2m через order_id на строке.

    В режиме staged пачка только складывается в staging (app/staging.py)."""
    if settings.ingest_mode == "staged":
        from backend.app import staging
        return await staging.stage_batch(
            "shipments", [_staged_row(_shipment_row(it), it) for it in items])

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
        shrunk = await _shrunk_documents(
            conn, t.shipment_lines, "shipment_id",
            {sid: {l["n4_id"] for l in lines} for sid, lines in by_ship.items()})
        await _record_direct(conn, "shipments", len(items), by_ship, affected, shrunk)
        if affected:
            await conn.execute(
                delete(t.shipment_lines).where(t.shipment_lines.c.shipment_id.in_(affected))
            )
        if rows:
            await conn.execute(insert(t.shipment_lines), rows)
    return {"accepted": len(rows), "shipments_affected": len(affected),
            "status": "applied" if items else "empty", "documents_shrunk": len(shrunk)}


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
    # Ниша из client_niche (мода по заказам ∪ отгрузкам): резолвится даже до появления
    # отгрузок у клиента (иначе анализ по нише молча отключался бы).
    q = select(t.client_niche.c.niche).where(t.client_niche.c.client_id == client_id)
    async with engine.connect() as conn:
        return (await conn.execute(q)).scalar_one_or_none()


async def get_client_summary(
    client_id: str,
    period_from: date | None = None,
    period_to: date | None = None,
) -> dict:
    """Клиентская сводка по реализованной истории ``shipment_lines``.

    При переданном периоде все показатели ограничиваются им (обе границы
    включительно). Без периода сохраняется прежний контракт: тоннаж за последние
    12 месяцев, остальные показатели — по всей истории.
    """
    params: dict[str, Any] = {"cid": client_id}
    if period_from is not None or period_to is not None:
        conditions = ["client_id = :cid"]
        if period_from is not None:
            conditions.append("shipment_date >= :period_from")
            params["period_from"] = period_from
        if period_to is not None:
            conditions.append("shipment_date <= :period_to")
            params["period_to"] = period_to
        sql = text(
            f"""
            SELECT
                COALESCE(sum(qty), 0)       AS shipped_qty,
                max(shipment_date)          AS last_shipment_date,
                count(DISTINCT order_id)    AS orders_in_history,
                count(DISTINCT shipment_id) AS shipments_in_history
            FROM shipment_lines
            WHERE {' AND '.join(conditions)}
            """
        )
    else:
        sql = text(
            """
            SELECT
                COALESCE(sum(qty) FILTER (
                    WHERE shipment_date >= CURRENT_DATE - INTERVAL '1 year'), 0) AS shipped_qty,
                max(shipment_date)          AS last_shipment_date,
                count(DISTINCT order_id)    AS orders_in_history,
                count(DISTINCT shipment_id) AS shipments_in_history
            FROM shipment_lines WHERE client_id = :cid
            """
        )
    async with engine.connect() as conn:
        r = (await conn.execute(sql, params)).one()
    return {
        "shipped_qty": round(float(r.shipped_qty or 0), 3),
        "last_shipment_date": r.last_shipment_date.isoformat() if r.last_shipment_date else None,
        "orders_in_history": int(r.orders_in_history or 0),
        "shipments_in_history": int(r.shipments_in_history or 0),
    }


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


async def get_client_profile_n4(
    client_id: str,
    period_from: date | None = None,
    period_to: date | None = None,
) -> list[dict]:
    if period_from is not None or period_to is not None:
        conditions = ["client_id = :client_id"]
        params: dict[str, Any] = {"client_id": client_id}
        if period_from is not None:
            conditions.append("shipment_date >= :period_from")
            params["period_from"] = period_from
        if period_to is not None:
            conditions.append("shipment_date <= :period_to")
            params["period_to"] = period_to
        sql = text(
            f"""
            WITH filtered AS (
                SELECT * FROM shipment_lines
                WHERE {' AND '.join(conditions)}
            ),
            totals AS (
                SELECT count(DISTINCT order_id) AS total_orders,
                       count(DISTINCT shipment_id) AS total_shipments
                FROM filtered
            ),
            per_n4 AS (
                SELECT max(n3_id) AS n3_id,
                       n4_id,
                       count(DISTINCT order_id) AS order_count,
                       count(DISTINCT shipment_id) AS shipment_count,
                       sum(qty) AS total_qty
                FROM filtered
                GROUP BY n4_id
            )
            SELECT p.n3_id, p.n4_id, p.order_count, p.shipment_count,
                   p.total_qty, p.total_qty AS total_qty_12m,
                   t.total_orders, t.total_shipments,
                   round(p.order_count::numeric / NULLIF(t.total_orders, 0) * 100, 1)
                       AS frequency_pct,
                   round(p.total_qty / NULLIF(p.order_count, 0), 3)
                       AS avg_qty_per_order
            FROM per_n4 p CROSS JOIN totals t
            """
        )
        async with engine.connect() as conn:
            rows = (await conn.execute(sql, params)).all()
        return [_row_to_client_profile_n4(r) for r in rows]

    q = select(t.client_profile_n4).where(t.client_profile_n4.c.client_id == client_id)
    async with engine.connect() as conn:
        rows = (await conn.execute(q)).all()
    return [_row_to_client_profile_n4(r) for r in rows]


async def get_niche_profile_n4(
    niche: str,
    period_from: date | None = None,
    period_to: date | None = None,
) -> list[dict]:
    if period_from is not None or period_to is not None:
        conditions = ["niche = :niche"]
        params: dict[str, Any] = {"niche": niche}
        if period_from is not None:
            conditions.append("shipment_date >= :period_from")
            params["period_from"] = period_from
        if period_to is not None:
            conditions.append("shipment_date <= :period_to")
            params["period_to"] = period_to
        sql = text(
            f"""
            WITH filtered AS (
                SELECT * FROM shipment_lines
                WHERE {' AND '.join(conditions)}
            ),
            totals AS (
                SELECT count(DISTINCT client_id) AS total_clients_in_niche,
                       count(DISTINCT order_id) AS total_orders_in_niche,
                       count(DISTINCT shipment_id) AS total_shipments_in_niche
                FROM filtered
            ),
            per_n4 AS (
                SELECT max(n3_id) AS n3_id,
                       n4_id,
                       count(DISTINCT client_id) AS client_count,
                       count(DISTINCT order_id) AS order_count,
                       count(DISTINCT shipment_id) AS shipment_count,
                       sum(qty) AS total_qty
                FROM filtered
                GROUP BY n4_id
            )
            SELECT p.n3_id, p.n4_id, p.client_count, p.order_count,
                   p.shipment_count, p.total_qty, p.total_qty AS total_qty_12m,
                   t.total_clients_in_niche, t.total_orders_in_niche,
                   t.total_shipments_in_niche,
                   round(p.client_count::numeric
                         / NULLIF(t.total_clients_in_niche, 0) * 100, 1) AS niche_pct,
                   round(p.order_count::numeric
                         / NULLIF(t.total_orders_in_niche, 0) * 100, 1) AS niche_freq_pct,
                   round(p.total_qty / NULLIF(p.client_count, 0), 3) AS avg_qty_per_client
            FROM per_n4 p CROSS JOIN totals t
            """
        )
        async with engine.connect() as conn:
            rows = (await conn.execute(sql, params)).all()
        return [_row_to_niche_profile_n4(r) for r in rows]

    q = select(t.niche_profile_n4).where(t.niche_profile_n4.c.niche == niche)
    async with engine.connect() as conn:
        rows = (await conn.execute(q)).all()
    return [_row_to_niche_profile_n4(r) for r in rows]


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


# --- BI reads (витрина эффекта + журнал рекомендаций) ---------------------

_FUNNEL_COLS = (
    "order_id", "n3_id", "client_id", "niche", "period_month",
    "qty_original", "qty_final", "qty_added", "qty_shipped",
    "is_original", "is_final", "is_new_position", "is_qty_increased", "is_shipped",
)


def _row_to_funnel(r) -> dict:
    d = r._mapping
    out = {c: d[c] for c in _FUNNEL_COLS}
    out["period_month"] = out["period_month"].isoformat() if out["period_month"] else None
    for q in ("qty_original", "qty_final", "qty_added", "qty_shipped"):
        out[q] = _f(out[q])
    return out


async def get_effect_funnel(
    niche: str | None = None,
    period_from: date | None = None,
    period_to: date | None = None,
    limit: int = 1000,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """Строки витрины эффекта (грейн order_id × n3_id) для выгрузки в BI, с
    пагинацией и необязательными фильтрами по нише/периоду. Возвращает (строки, всего)."""
    ef = t.effect_funnel
    conds = []
    if niche:
        conds.append(ef.c.niche == niche)
    if period_from:
        conds.append(ef.c.period_month >= period_from)
    if period_to:
        conds.append(ef.c.period_month <= period_to)
    q = select(ef)
    cq = select(func.count()).select_from(ef)
    for c in conds:
        q = q.where(c)
        cq = cq.where(c)
    q = q.order_by(ef.c.order_id, ef.c.n3_id).limit(limit).offset(offset)
    async with engine.connect() as conn:
        rows = (await conn.execute(q)).all()
        total = int(await conn.scalar(cq))
    return [_row_to_funnel(r) for r in rows], total


def _row_to_reclog(r) -> dict:
    return {
        "id": int(r.id),
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "client_id": r.client_id,
        "order_id": r.order_id,
        "kind": r.kind,
        "n3_id": r.n3_id,
        "n4_id": r.n4_id,
        "priority": r.priority,
    }


async def get_recommendation_log(
    since_id: int | None = None,
    kind: str | None = None,
    limit: int = 1000,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """Журнал выданных рекомендаций для BI (append-only). ``since_id`` — инкрементная
    выгрузка (строки с id больше указанного). Возвращает (строки, всего по фильтру)."""
    rl = t.recommendation_log
    conds = []
    if since_id is not None:
        conds.append(rl.c.id > since_id)
    if kind:
        conds.append(rl.c.kind == kind)
    q = select(rl)
    cq = select(func.count()).select_from(rl)
    for c in conds:
        q = q.where(c)
        cq = cq.where(c)
    q = q.order_by(rl.c.id).limit(limit).offset(offset)
    async with engine.connect() as conn:
        rows = (await conn.execute(q)).all()
        total = int(await conn.scalar(cq))
    return [_row_to_reclog(r) for r in rows], total


def _row_to_recommendation_effect_n4(r) -> dict:
    return {
        "recommendation_id": int(r.recommendation_id),
        "recommendation_at": r.recommendation_at.isoformat() if r.recommendation_at else None,
        "client_id": r.client_id,
        "niche": r.niche,
        "kind": r.kind,
        "scenario": r.scenario,
        "source_order_id": r.source_order_id,
        "target_order_id": r.target_order_id,
        "n3_id": r.n3_id,
        "n4_id": r.n4_id,
        "target_order_date": r.target_order_date.isoformat() if r.target_order_date else None,
        "ordered_qty": _f(r.ordered_qty),
        "shipped_qty": _f(r.shipped_qty),
        "realized_qty": _f(r.realized_qty),
    }


async def get_recommendation_effect_n4(
    niche: str | None = None,
    scenario: str | None = None,
    period_from: date | None = None,
    period_to: date | None = None,
    limit: int = 1000,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """Confirmed direct effect for recommended N4 positions only."""
    view = t.recommendation_effect_n4
    conds = []
    if niche:
        conds.append(view.c.niche == niche)
    if scenario:
        conds.append(view.c.scenario == scenario)
    if period_from:
        conds.append(view.c.target_order_date >= period_from)
    if period_to:
        conds.append(view.c.target_order_date <= period_to)
    q = select(view)
    cq = select(func.count()).select_from(view)
    for condition in conds:
        q = q.where(condition)
        cq = cq.where(condition)
    q = q.order_by(
        view.c.target_order_date, view.c.target_order_id, view.c.n4_id
    ).limit(limit).offset(offset)
    async with engine.connect() as conn:
        rows = (await conn.execute(q)).all()
        total = int(await conn.scalar(cq))
    return [_row_to_recommendation_effect_n4(r) for r in rows], total


# --- Aggregates -----------------------------------------------------------

async def count_order_lines() -> int:
    async with engine.connect() as conn:
        return int(await conn.scalar(text("SELECT count(*) FROM order_lines")))


# Стабильный ключ session-level advisory-lock: сериализует перекрывающиеся refresh.
_REFRESH_LOCK_KEY = 776610


async def refresh_aggregates() -> None:
    """Rebuild all materialized views in dependency order (CONCURRENTLY once
    populated; AUTOCOMMIT since CONCURRENTLY can't run in a transaction).

    Три пути зовут refresh (воркер по таймеру, /commit, /refresh). Берём
    блокирующий advisory-lock, чтобы одновременно шёл ровно один rebuild: иначе два
    ``REFRESH … CONCURRENTLY`` одного матвью конфликтуют на его блокировке, а данные
    всё равно перестраиваются дважды. Блокирующий (а не try) — чтобы refresh после
    /commit гарантированно отработал и включил только что загруженные данные."""
    ac_engine = engine.execution_options(isolation_level="AUTOCOMMIT")
    async with ac_engine.connect() as conn:
        await conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": _REFRESH_LOCK_KEY})
        try:
            for mv in t.MATERIALIZED_VIEWS:
                populated = await conn.scalar(
                    text("SELECT ispopulated FROM pg_matviews WHERE matviewname = :n"),
                    {"n": mv},
                )
                mode = "CONCURRENTLY " if populated else ""
                await conn.execute(text(f"REFRESH MATERIALIZED VIEW {mode}{mv}"))
        finally:
            await conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _REFRESH_LOCK_KEY})
