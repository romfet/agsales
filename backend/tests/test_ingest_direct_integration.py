"""Integration tests: режим direct — статистика приёма (защита от передачи документа
частями) и отложенная пересборка агрегатов по /commit."""
import os
import time

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy import text

from backend.app import repository as repo
from backend.app import staging
from backend.app.core.config import settings
from backend.app.db.session import engine

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

_DB = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")


@pytest.fixture(autouse=True)
async def _direct(monkeypatch):
    if not _DB:
        pytest.skip("no DATABASE_URL / TEST_DATABASE_URL set")
    monkeypatch.setattr(settings, "ingest_mode", "direct")
    monkeypatch.setattr(settings, "ingest_commit_refresh", "deferred")
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE ingest_direct_log, ingest_batches, staging_order_lines, "
                                "staging_shipment_lines, staging_applied, ingest_merges"))
    yield


def _client():
    from backend.app.main import app
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def sl(sid, n4, qty=1.0, client="cli-dir"):
    return {"shipment_id": sid, "shipment_num": sid, "shipment_date": "2026-10-20",
            "order_id": "ord-" + sid, "client_id": client, "niche": "niche-dir",
            "n3_id": "n3-dir", "n4_id": n4, "qty": qty}


def ol(oid, n4, qty=1.0):
    return {"order_id": oid, "order_num": oid, "order_date": "2026-10-20", "client_id": "cli-dir",
            "niche": "niche-dir", "n3_id": "n3-dir", "n4_id": n4, "qty": qty}


async def q(sql, **p):
    async with engine.begin() as conn:
        res = await conn.execute(text(sql), p)
        return res.all() if res.returns_rows else []


# --- Защита: документ, переданный частями, виден в /stats -----------------------

async def test_split_document_is_visible_in_stats():
    """Старое поведение 1С: РТУ из трёх товаров тремя запросами."""
    async with _client() as c:
        for n4 in ("a", "b", "c"):
            r = await c.post("/api/ingest/shipments", json={"items": [sl("DIR-SPLIT", n4)]})
            assert r.status_code == 200
        assert r.json()["documents_shrunk"] == 1
        st = (await c.get("/api/ingest/stats")).json()["direct_last_24h"]["shipments"]
    assert st["batches"] == 3 and st["documents"] == 3
    assert st["documents_shrunk"] == 2                       # 2-я и 3-я пачки сузили РТУ
    assert st["single_product_share"] == 1.0
    assert st["shrunk_sample"] == "DIR-SPLIT"


async def test_document_per_request_is_clean_in_stats():
    """Новое поведение 1С: документ целиком в одном запросе, повторно — тоже целиком."""
    doc = [sl("DIR-WHOLE", "a"), sl("DIR-WHOLE", "b"), sl("DIR-WHOLE", "c")]
    other = [ol("DIR-ORD", "a"), ol("DIR-ORD", "b")]
    async with _client() as c:
        await c.post("/api/ingest/shipments", json={"items": doc})
        await c.post("/api/ingest/shipments", json={"items": doc})   # повторная ночная выгрузка
        await c.post("/api/ingest/order-lines", json={"items": other})
        st = (await c.get("/api/ingest/stats")).json()["direct_last_24h"]
    assert st["shipments"]["documents_shrunk"] == 0
    assert st["shipments"]["single_product_share"] == 0.0
    assert st["orders"]["documents"] == 1 and st["orders"]["documents_shrunk"] == 0
    rows = await q("SELECT count(*) FROM shipment_lines WHERE shipment_id = 'DIR-WHOLE'")
    assert rows[0][0] == 3


async def test_empty_batch_and_staged_mode_do_not_write_direct_log(monkeypatch):
    await repo.ingest_shipment_lines([])
    monkeypatch.setattr(settings, "ingest_mode", "staged")
    await repo.ingest_shipment_lines([sl("DIR-STAGED", "a")])
    assert (await q("SELECT count(*) FROM ingest_direct_log"))[0][0] == 0


async def test_cleanup_removes_old_direct_log():
    await repo.ingest_shipment_lines([sl("DIR-OLD", "a")])
    await repo.ingest_shipment_lines([sl("DIR-NEW", "a")])
    await q("UPDATE ingest_direct_log SET received_at = now() - interval '30 days' "
            "WHERE id = (SELECT min(id) FROM ingest_direct_log)")
    await staging.cleanup_staging()
    assert (await q("SELECT count(*) FROM ingest_direct_log"))[0][0] == 1
    async with _client() as c:                               # старые строки не в сутках
        st = (await c.get("/api/ingest/stats")).json()["direct_last_24h"]["shipments"]
    assert st["batches"] == 1


# --- Производительность: /commit без синхронной пересборки ------------------------

async def _n4_in_catalog(n4) -> bool:
    return bool(await q("SELECT 1 FROM products WHERE n4_id = :n", n=n4))


async def test_commit_deferred_answers_at_once_without_refresh():
    await repo.refresh_aggregates()                          # матвью заполнены
    await repo.ingest_shipment_lines([sl("DIR-DEF", "n4-deferred-only")])
    async with _client() as c:
        t0 = time.perf_counter()
        r = await c.post("/api/ingest/commit")
        dt = time.perf_counter() - t0
    assert r.status_code == 200
    assert r.json()["status"] == "ok" and r.json()["refresh"] == "deferred"
    assert isinstance(r.json()["rows"], int)
    assert not await _n4_in_catalog("n4-deferred-only")      # агрегаты не пересобраны
    await repo.refresh_aggregates()                          # как сделает worker
    assert await _n4_in_catalog("n4-deferred-only")
    assert dt < 2.0


async def test_commit_sync_refreshes_in_request(monkeypatch):
    monkeypatch.setattr(settings, "ingest_commit_refresh", "sync")
    await repo.ingest_shipment_lines([sl("DIR-SYNC", "n4-sync-only")])
    async with _client() as c:
        r = await c.post("/api/ingest/commit")
    assert r.status_code == 200 and r.json()["refresh"] == "done"
    assert await _n4_in_catalog("n4-sync-only")


async def test_feed_like_1c_with_commit_after_each_batch_deferred_is_fast(monkeypatch):
    """1С: пачка + commit на каждую. deferred: commit не пересобирает 10 матвью."""
    async def timed_feed(prefix):
        t0 = time.perf_counter()
        async with _client() as c:
            for i in range(10):
                await c.post("/api/ingest/shipments",
                             json={"items": [sl(f"{prefix}-{i}", "a"), sl(f"{prefix}-{i}", "b")]})
                assert (await c.post("/api/ingest/commit")).status_code == 200
        return time.perf_counter() - t0

    fast = await timed_feed("DIR-FAST")
    monkeypatch.setattr(settings, "ingest_commit_refresh", "sync")
    slow = await timed_feed("DIR-SLOW")
    assert fast < slow                                       # пересборка в каждом commit дороже
