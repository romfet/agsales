"""Приём данных 1С в режиме staged: пачки → staging → сборка документов.

См. docs/SDD_сборка_документов_при_приёме.md. 1С передаёт строки одного документа
(заказ / РТУ) в разных запросах, поэтому запрос нельзя считать полным составом
документа. Пачки складываются в staging без изменения боевых таблиц; worker собирает
их (``merge_staging``): по каждому товару (n4_id) документа берутся строки из
последней пачки, где этот товар встретился. Позиции, которых нет в загрузке, по
умолчанию сохраняются (``INGEST_SHRINK_MODE=keep``) — пропуск строки при выгрузке
неотличим от удаления позиции в 1С.
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import insert, text

from backend.app.core.config import settings
from backend.app.db import tables as t
from backend.app.db.session import engine

logger = logging.getLogger("ingest.staging")

# Ключ advisory-lock сборки (второй аргумент — вид данных). Отличается от ключа
# refresh_aggregates (776610), чтобы сборка и пересчёт агрегатов не ждали друг друга.
_MERGE_LOCK_KEY = 776611
_AUTO_TRIGGERS = ("idle", "max_delay")
_INSERT_CHUNK = 5000


@dataclass(frozen=True)
class _Kind:
    name: str            # orders | shipments
    lock: int
    staging: str
    target: str
    doc: str             # ключ документа
    cols: tuple[str, ...]
    staging_table: Any
    affected_key: str    # поле ответа ingest с числом документов


KINDS: dict[str, _Kind] = {
    "orders": _Kind(
        "orders", 1, "staging_order_lines", "order_lines", "order_id",
        ("order_id", "order_num", "order_date", "client_id", "client_name",
         "niche", "n3_id", "n4_id", "qty"),
        t.staging_order_lines, "orders_affected",
    ),
    "shipments": _Kind(
        "shipments", 2, "staging_shipment_lines", "shipment_lines", "shipment_id",
        ("shipment_id", "shipment_num", "shipment_date", "order_id", "client_id",
         "client_name", "niche", "n3_id", "n4_id", "qty"),
        t.staging_shipment_lines, "shipments_affected",
    ),
}


def _payload_hash(rows: list[dict]) -> str:
    """Хэш пачки, не зависящий от порядка строк: повтор той же пачки = тот же хэш."""
    parts = sorted(json.dumps(r, sort_keys=True, default=str, ensure_ascii=False) for r in rows)
    return hashlib.md5("\n".join(parts).encode()).hexdigest()


# --- Приём пачки ------------------------------------------------------------

async def stage_batch(kind: str, rows: list[dict]) -> dict:
    """Сохранить пачку в staging. ``rows`` — уже нормализованные строки (даты
    разобраны) с полем ``deleted``. Боевые таблицы не меняются."""
    k = KINDS[kind]
    if not rows:
        return {"accepted": 0, k.affected_key: 0, "batch_id": None,
                "status": "empty", "duplicate_of": None}

    docs = {r[k.doc] for r in rows}
    phash = _payload_hash(rows)
    async with engine.begin() as conn:
        for _ in range(2):
            batch_id = (await conn.execute(text(
                """
                INSERT INTO ingest_batches (kind, payload_hash, items)
                VALUES (:kind, :h, :n)
                ON CONFLICT (kind, payload_hash) WHERE merged_at IS NULL DO NOTHING
                RETURNING batch_id
                """), {"kind": kind, "h": phash, "n": len(rows)})).scalar()
            if batch_id is not None:
                break
            dup = (await conn.execute(text(
                "SELECT batch_id FROM ingest_batches "
                "WHERE kind = :kind AND payload_hash = :h AND merged_at IS NULL"),
                {"kind": kind, "h": phash})).scalar()
            if dup is not None:
                # Повтор несобранной пачки (ретрай 1С): второй раз не пишем.
                return {"accepted": len(rows), k.affected_key: len(docs),
                        "batch_id": None, "status": "duplicate", "duplicate_of": dup}
            # Дубль успели собрать между INSERT и SELECT — пробуем вставить ещё раз.
        await conn.execute(insert(k.staging_table),
                           [{**r, "batch_id": batch_id} for r in rows])
    return {"accepted": len(rows), k.affected_key: len(docs), "batch_id": batch_id,
            "status": "staged", "duplicate_of": None}


# --- Сборка -----------------------------------------------------------------

def _effective_shrink(trigger: str) -> str:
    mode = settings.ingest_shrink_mode
    if mode == "replace" or (mode == "replace_on_commit" and trigger == "commit"):
        return "replace"
    return "keep"


async def merge_staging(kind: str, trigger: str = "manual",
                        merge_id: int | None = None) -> dict | None:
    """Собрать все несобранные пачки ``kind`` в боевую таблицу.

    Одна транзакция под xact advisory-lock. Возвращает сводку сборки или None, если
    собирать нечего. ``merge_id`` — заранее созданный запрос сборки (commit)."""
    k = KINDS[kind]
    try:
        return await _merge(k, trigger, merge_id)
    except Exception as e:
        logger.exception("ingest merge failed: kind=%s trigger=%s", kind, trigger)
        async with engine.begin() as conn:
            if merge_id is None:
                await conn.execute(text(
                    "INSERT INTO ingest_merges (kind, trigger, started_at, finished_at, error) "
                    "VALUES (:k, :tr, now(), now(), :e)"), {"k": kind, "tr": trigger, "e": str(e)})
            else:
                await conn.execute(text(
                    "UPDATE ingest_merges SET started_at = now(), finished_at = now(), error = :e "
                    "WHERE merge_id = :m"), {"m": merge_id, "e": str(e)})
        raise


async def _merge(k: _Kind, trigger: str, merge_id: int | None) -> dict | None:
    shrink = _effective_shrink(trigger)
    keep = shrink == "keep"
    cols = ", ".join(k.cols)
    s_cols = ", ".join(f"s.{c}" for c in k.cols)
    d = k.doc

    async with engine.begin() as conn:
        x = conn.execute
        await x(text("SELECT pg_advisory_xact_lock(CAST(:a AS int), CAST(:b AS int))"), {"a": _MERGE_LOCK_KEY, "b": k.lock})

        # 1. Снимок загрузки — явным списком пачек.
        await x(text(
            "CREATE TEMP TABLE _snap ON COMMIT DROP AS "
            "SELECT batch_id, received_at FROM ingest_batches "
            "WHERE kind = :k AND merged_at IS NULL"), {"k": k.name})
        n_batches = (await x(text("SELECT count(*) FROM _snap"))).scalar()
        if not n_batches:
            if merge_id is not None:
                await x(text(
                    "UPDATE ingest_merges SET started_at = now(), finished_at = now(), "
                    "batches = 0, documents = 0, lines = 0 WHERE merge_id = :m"), {"m": merge_id})
            await _close_commit_requests(conn, k.name)
            return None

        if merge_id is None:
            merge_id = (await x(text(
                "INSERT INTO ingest_merges (kind, trigger, started_at, shrink_mode) "
                "VALUES (:k, :tr, now(), :sh) RETURNING merge_id"),
                {"k": k.name, "tr": trigger, "sh": shrink})).scalar()
        else:
            await x(text(
                "UPDATE ingest_merges SET started_at = now(), shrink_mode = :sh "
                "WHERE merge_id = :m"), {"m": merge_id, "sh": shrink})

        await x(text(
            f"CREATE TEMP TABLE _load ON COMMIT DROP AS "
            f"SELECT s.*, n.received_at FROM {k.staging} s JOIN _snap n USING (batch_id)"))
        await x(text("ANALYZE _load"))
        await x(text("CREATE TEMP TABLE _docs ON COMMIT DROP AS "
                     f"SELECT DISTINCT {d} AS doc_id FROM _load"))
        # 2. Удаление документа (deleted=true): строки до последней отметки отбрасываются.
        await x(text(
            "CREATE TEMP TABLE _del ON COMMIT DROP AS "
            f"SELECT {d} AS doc_id, max(batch_id) AS del_batch FROM _load "
            f"WHERE deleted GROUP BY {d}"))
        await x(text(
            "CREATE TEMP TABLE _lines ON COMMIT DROP AS "
            f"SELECT l.* FROM _load l LEFT JOIN _del dl ON dl.doc_id = l.{d} "
            "WHERE NOT l.deleted AND (dl.del_batch IS NULL OR l.batch_id > dl.del_batch)"))
        # 3. По каждому товару документа — последняя пачка, где он встретился.
        #    overridden: в БД уже лежит строка из более новой пачки (поздняя старая пачка).
        # Все проверки — соединениями по ключу (doc_id, n4_id): коррелированные подзапросы
        # на строку дают O(n·m) на полной выгрузке (сотни тысяч документов).
        await x(text(
            "CREATE TEMP TABLE _pick ON COMMIT DROP AS "
            "SELECT p.doc_id, p.n4_id, p.batch_id, "
            "       COALESCE(a.batch_id > p.batch_id, false) AS overridden "
            f"FROM (SELECT {d} AS doc_id, n4_id, max(batch_id) AS batch_id "
            f"      FROM _lines GROUP BY {d}, n4_id) p "
            "LEFT JOIN staging_applied a "
            "  ON a.kind = :k AND a.doc_id = p.doc_id AND a.n4_id = p.n4_id"), {"k": k.name})
        await x(text("ANALYZE _pick"))
        await x(text(
            f"CREATE TEMP TABLE _new ON COMMIT DROP AS SELECT {s_cols} FROM _lines s "
            f"JOIN _pick p ON p.doc_id = s.{d} AND p.n4_id = s.n4_id AND p.batch_id = s.batch_id "
            "WHERE NOT p.overridden"))
        await x(text(
            f"CREATE TEMP TABLE _db ON COMMIT DROP AS SELECT {s_cols} FROM {k.target} s "
            f"JOIN _docs dc ON dc.doc_id = s.{d}"))
        await x(text("ANALYZE _db"))
        # Позиции из БД, которых нет в загрузке (документ не удалялся в этой загрузке).
        # stale: все присланные строки документа перекрыты более новыми в БД —
        # устаревшая пачка документ не сужает даже в режиме replace.
        await x(text(
            "CREATE TEMP TABLE _absent ON COMMIT DROP AS "
            f"SELECT DISTINCT b.{d} AS doc_id, b.n4_id, (fr.doc_id IS NULL) AS stale "
            f"FROM _db b "
            f"LEFT JOIN _pick p ON p.doc_id = b.{d} AND p.n4_id = b.n4_id "
            f"LEFT JOIN _del dl ON dl.doc_id = b.{d} "
            f"LEFT JOIN (SELECT DISTINCT doc_id FROM _pick WHERE NOT overridden) fr "
            f"  ON fr.doc_id = b.{d} "
            f"WHERE p.doc_id IS NULL AND dl.doc_id IS NULL"))
        await x(text("ANALYZE _absent"))

        # 4. Итоговый состав: присланное + (более новые строки БД) + (отсутствующие при keep).
        await x(text(
            f"CREATE TEMP TABLE _final ON COMMIT DROP AS "
            f"SELECT {cols} FROM _new "
            f"UNION ALL "
            f"SELECT {s_cols} FROM _db s "
            f"LEFT JOIN _pick p ON p.doc_id = s.{d} AND p.n4_id = s.n4_id "
            f"LEFT JOIN _absent a ON a.doc_id = s.{d} AND a.n4_id = s.n4_id "
            f"WHERE COALESCE(p.overridden, false) "
            f"   OR (a.doc_id IS NOT NULL AND (:keep OR a.stale))"),
            {"keep": keep})

        stats = (await x(text(
            f"""
            SELECT
              (SELECT count(*) FROM _docs) AS documents,
              (SELECT count(*) FROM _final) AS lines,
              (SELECT count(*) FROM _absent WHERE :keep OR stale) AS kept_positions,
              (SELECT count(DISTINCT doc_id) FROM _absent WHERE NOT (:keep OR stale)) AS shrunk_docs,
              (SELECT count(DISTINCT n.doc_id) FROM (
                  SELECT {d} AS doc_id, n4_id, count(*) c, sum(qty) q FROM _new GROUP BY 1, 2) n
               JOIN (SELECT {d} AS doc_id, n4_id, count(*) c, sum(qty) q FROM _db GROUP BY 1, 2) b
                 USING (doc_id, n4_id)
               WHERE n.q < b.q OR n.c < b.c) AS qty_decreased,
              (SELECT round(avg(CASE WHEN n = 1 THEN 1.0 ELSE 0.0 END), 4) FROM (
                  SELECT {d}, count(DISTINCT n4_id) n FROM _final GROUP BY 1) z) AS single_share
            """), {"keep": keep})).one()

        # 5. Применение.
        await x(text(f"DELETE FROM {k.target} WHERE {d} IN (SELECT doc_id FROM _docs)"))
        await x(text(f"INSERT INTO {k.target} ({cols}) SELECT {cols} FROM _final"))
        await x(text(
            "INSERT INTO staging_applied (kind, doc_id, n4_id, batch_id) "
            "SELECT :k, doc_id, n4_id, batch_id FROM _pick WHERE NOT overridden "
            "ON CONFLICT (kind, doc_id, n4_id) DO UPDATE "
            "SET batch_id = GREATEST(staging_applied.batch_id, EXCLUDED.batch_id)"), {"k": k.name})

        # 6. Версии заказов — одна на сборку.
        versions = await _write_versions(conn, merge_id) if k.name == "orders" else 0

        # 7. Закрыть загрузку.
        await x(text(
            "UPDATE ingest_batches SET merged_at = now(), merge_id = :m "
            "WHERE batch_id IN (SELECT batch_id FROM _snap)"), {"m": merge_id})
        await x(text(f"DELETE FROM {k.staging} WHERE batch_id IN (SELECT batch_id FROM _snap)"))

        summary = {
            "merge_id": merge_id, "kind": k.name, "trigger": trigger, "shrink_mode": shrink,
            "batches": int(n_batches), "documents": int(stats.documents), "lines": int(stats.lines),
            "documents_shrunk": int(stats.shrunk_docs),
            "documents_qty_decreased": int(stats.qty_decreased),
            "positions_kept": int(stats.kept_positions),
            "single_product_share": float(stats.single_share) if stats.single_share is not None else None,
            "versions": versions,
        }
        await x(text(
            """
            UPDATE ingest_merges SET finished_at = clock_timestamp(), batches = :batches,
              documents = :documents, lines = :lines, documents_shrunk = :documents_shrunk,
              documents_qty_decreased = :documents_qty_decreased,
              positions_kept = :positions_kept, single_product_share = :single_product_share
            WHERE merge_id = :merge_id
            """), summary)
        await _close_commit_requests(conn, k.name)

    level = logging.WARNING if (summary["documents_shrunk"] or summary["documents_qty_decreased"]) \
        else logging.INFO
    logger.log(level, "ingest merge %s", summary)
    return summary


async def _close_commit_requests(conn, kind: str) -> None:
    """Запросы сборки по commit, поступившие до этой сборки, ею закрыты."""
    await conn.execute(text(
        "UPDATE ingest_merges SET started_at = now(), finished_at = now() "
        "WHERE kind = :k AND started_at IS NULL"), {"k": kind})


def _multiset(lines: list[dict]) -> Counter:
    return Counter((l["n4_id"], round(float(l["qty"]), 3)) for l in lines)


async def _write_versions(conn, merge_id: int) -> int:
    """Версия заказа по итоговому составу (тот же, что в order_lines), если состав
    изменился. Заказ, разрезанный границей автосборки, не плодит «добавленные»
    позиции: если прошлая версия создана автосборкой недавно, а новый состав — её
    надмножество, прошлая версия заменяется (тот же version_seq и ingested_at)."""
    from backend.app.repository import _content_hash  # локально: repository → staging

    x = conn.execute
    rows = (await x(text(
        "SELECT order_id, order_num, order_date, client_id, client_name, niche, "
        "n3_id, n4_id, qty FROM _final ORDER BY order_id"))).mappings().all()
    if not rows:
        return 0
    by_order: dict[str, list[dict]] = {}
    for r in rows:
        by_order.setdefault(r["order_id"], []).append(dict(r))

    first_seen = {r.order_id: r.at for r in (await x(text(
        "SELECT order_id, min(received_at) AS at FROM _load GROUP BY order_id"))).all()}
    now = (await x(text("SELECT now()"))).scalar()
    window = timedelta(minutes=settings.ingest_version_coalesce_minutes)

    await x(text("CREATE TEMP TABLE _vdocs ON COMMIT DROP AS SELECT DISTINCT order_id FROM _final"))
    last_rows = (await x(text(
        """
        WITH last AS (
            SELECT DISTINCT ON (v.order_id) v.order_id, v.version_seq
            FROM order_line_versions v JOIN _vdocs USING (order_id)
            ORDER BY v.order_id, v.version_seq DESC
        )
        SELECT v.order_id, v.version_seq, v.ingested_at, v.content_hash, m.trigger,
               v.n4_id, v.qty
        FROM order_line_versions v JOIN last USING (order_id, version_seq)
        LEFT JOIN ingest_merges m ON m.merge_id = v.merge_id
        """))).mappings().all()
    last: dict[str, dict] = {}
    for r in last_rows:
        e = last.setdefault(r["order_id"], {
            "seq": r["version_seq"], "at": r["ingested_at"], "hash": r["content_hash"],
            "trigger": r["trigger"], "lines": []})
        e["lines"].append(r)

    seq: int | None = None
    replace_keys: list[tuple[str, int]] = []
    new_rows: list[dict] = []
    for oid, lines in by_order.items():
        h = _content_hash(lines)
        prev = last.get(oid)
        if prev and prev["hash"] == h:
            continue
        if (prev and prev["trigger"] in _AUTO_TRIGGERS and prev["at"] >= now - window
                and not (_multiset(prev["lines"]) - _multiset(lines))):
            replace_keys.append((oid, prev["seq"]))
            vseq, at = prev["seq"], prev["at"]
        else:
            if seq is None:
                seq = (await x(text("SELECT nextval('order_version_seq')"))).scalar()
            vseq, at = seq, first_seen.get(oid, now)
        new_rows.extend({**l, "version_seq": vseq, "ingested_at": at,
                         "content_hash": h, "merge_id": merge_id} for l in lines)

    for oid, vseq in replace_keys:
        await x(text("DELETE FROM order_line_versions WHERE order_id = :o AND version_seq = :s"),
                {"o": oid, "s": vseq})
    for i in range(0, len(new_rows), _INSERT_CHUNK):
        await x(insert(t.order_line_versions), new_rows[i:i + _INSERT_CHUNK])
    return len({(r["order_id"], r["version_seq"]) for r in new_rows})


# --- Триггеры (worker) ------------------------------------------------------

async def request_commit(kind: str | None) -> dict:
    """POST /commit в режиме staged. 1С вызывает commit после каждой пачки, поэтому
    по умолчанию он сборку не запускает (INGEST_MERGE_ON_COMMIT=false)."""
    kinds = [kind] if kind else list(KINDS)
    merge_ids: dict[str, int] = {}
    if settings.ingest_merge_on_commit:
        async with engine.begin() as conn:
            for kd in kinds:
                merge_ids[kd] = (await conn.execute(text(
                    "INSERT INTO ingest_merges (kind, trigger) VALUES (:k, 'commit') "
                    "RETURNING merge_id"), {"k": kd})).scalar()
    return {"status": "accepted", "merge_on_commit": settings.ingest_merge_on_commit,
            "merge_ids": merge_ids}


async def merge_tick(now: datetime | None = None) -> bool:
    """Один тик worker: собрать виды данных, для которых сработал триггер.
    Возвращает True, если что-то собрано (нужен пересчёт агрегатов)."""
    if settings.ingest_mode != "staged":
        return False
    merged = False
    for kind in KINDS:
        async with engine.connect() as conn:
            p = (await conn.execute(text(
                "SELECT count(*) AS n, min(received_at) AS oldest, max(received_at) AS newest, "
                "now() AS now FROM ingest_batches WHERE kind = :k AND merged_at IS NULL"),
                {"k": kind})).one()
            req = (await conn.execute(text(
                "SELECT min(merge_id) FROM ingest_merges WHERE kind = :k AND started_at IS NULL"),
                {"k": kind})).scalar()
        cur = now or p.now
        trigger, merge_id = None, None
        if req is not None and settings.ingest_merge_on_commit:
            trigger, merge_id = "commit", req
        elif p.n and cur - p.newest >= timedelta(minutes=settings.ingest_merge_idle_minutes):
            trigger = "idle"
        elif p.n and cur - p.oldest >= timedelta(minutes=settings.ingest_merge_max_delay_minutes):
            trigger = "max_delay"
        if trigger and await merge_staging(kind, trigger, merge_id):
            merged = True
    return merged


async def cleanup_staging() -> None:
    """Ретенция собранных пачек, меток staging_applied и статистики direct
    (раз в сутки, worker)."""
    async with engine.begin() as conn:
        await conn.execute(text(
            "DELETE FROM ingest_direct_log "
            "WHERE received_at < now() - make_interval(days => CAST(:d AS int))"),
            {"d": settings.ingest_batch_retention_days})
        await conn.execute(text(
            "DELETE FROM ingest_batches WHERE merged_at < now() - make_interval(days => CAST(:d AS int))"),
            {"d": settings.ingest_batch_retention_days})
        await conn.execute(text(
            "DELETE FROM staging_applied a WHERE NOT EXISTS "
            "(SELECT 1 FROM ingest_batches b WHERE b.batch_id = a.batch_id)"))
    ac = engine.execution_options(isolation_level="AUTOCOMMIT")
    async with ac.connect() as conn:
        for tbl in ("staging_order_lines", "staging_shipment_lines", "ingest_batches",
                    "staging_applied", "ingest_direct_log"):
            await conn.execute(text(f"VACUUM (ANALYZE) {tbl}"))


# --- Чтение (API) -------------------------------------------------------------

_MERGE_COLS = ("merge_id", "kind", "trigger", "requested_at", "started_at", "finished_at",
               "shrink_mode", "batches", "documents", "lines", "documents_shrunk",
               "documents_qty_decreased", "positions_kept", "single_product_share", "error")


def _merge_row(r) -> dict:
    out = {c: r[c] for c in _MERGE_COLS}
    for c in ("requested_at", "started_at", "finished_at"):
        out[c] = out[c].isoformat() if out[c] else None
    if out["single_product_share"] is not None:
        out["single_product_share"] = float(out["single_product_share"])
    return out


async def get_merge(merge_id: int) -> dict | None:
    async with engine.connect() as conn:
        r = (await conn.execute(text(
            f"SELECT {', '.join(_MERGE_COLS)} FROM ingest_merges WHERE merge_id = :m"),
            {"m": merge_id})).mappings().first()
    return _merge_row(r) if r else None


async def get_stats() -> dict:
    async with engine.connect() as conn:
        pend = (await conn.execute(text(
            "SELECT kind, count(*) AS batches, sum(items) AS items, "
            "min(received_at) AS oldest, max(received_at) AS newest "
            "FROM ingest_batches WHERE merged_at IS NULL GROUP BY kind"))).mappings().all()
        merges = (await conn.execute(text(
            f"SELECT {', '.join(_MERGE_COLS)} FROM ingest_merges "
            "ORDER BY merge_id DESC LIMIT 10"))).mappings().all()
        direct = (await conn.execute(text(
            "SELECT kind, count(*) AS batches, sum(items) AS items, sum(documents) AS documents, "
            "sum(documents_shrunk) AS shrunk, sum(single_product_docs) AS single, "
            "max(shrunk_sample) FILTER (WHERE documents_shrunk > 0) AS sample, "
            "max(received_at) AS last_at "
            "FROM ingest_direct_log WHERE received_at >= now() - interval '24 hours' "
            "GROUP BY kind"))).mappings().all()
    return {
        "ingest_mode": settings.ingest_mode,
        "commit_refresh": settings.ingest_commit_refresh,
        # Режим direct, последние сутки. documents_shrunk > 0 или высокая доля
        # однотоварных документов — 1С снова передаёт документы частями.
        "direct_last_24h": {
            r["kind"]: {
                "batches": int(r["batches"]), "items": int(r["items"]),
                "documents": int(r["documents"]), "documents_shrunk": int(r["shrunk"]),
                "single_product_share": round(int(r["single"]) / int(r["documents"]), 4)
                if r["documents"] else None,
                "shrunk_sample": r["sample"], "last_batch_at": r["last_at"].isoformat(),
            }
            for r in direct
        },
        "shrink_mode": settings.ingest_shrink_mode,
        "merge_on_commit": settings.ingest_merge_on_commit,
        "merge_idle_minutes": settings.ingest_merge_idle_minutes,
        "merge_max_delay_minutes": settings.ingest_merge_max_delay_minutes,
        "pending": {
            r["kind"]: {"batches": int(r["batches"]), "items": int(r["items"] or 0),
                        "oldest": r["oldest"].isoformat(), "newest": r["newest"].isoformat()}
            for r in pend
        },
        "recent_merges": [_merge_row(r) for r in merges],
    }
