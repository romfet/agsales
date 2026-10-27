"""Integration tests: приём в режиме staged и сборка документов (app/staging.py).

Синтетика повторяет то, что 1С делает на стенде: документ приходит по одному товару в
разных запросах, страницы повторяются, количество правится между проходами. Каждый
тест работает со своими order_id / shipment_id; staging очищается перед тестом.
"""
import asyncio
import os
from datetime import timedelta

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
async def _staged(monkeypatch):
    if not _DB:
        pytest.skip("no DATABASE_URL / TEST_DATABASE_URL set")
    monkeypatch.setattr(settings, "ingest_mode", "staged")
    monkeypatch.setattr(settings, "ingest_shrink_mode", "keep")
    monkeypatch.setattr(settings, "ingest_merge_on_commit", False)
    async with engine.begin() as conn:
        await conn.execute(text(
            "TRUNCATE ingest_batches, staging_order_lines, staging_shipment_lines, "
            "staging_applied, ingest_merges, ingest_direct_log"))
    yield


def ol(oid, n4, qty, n3="n3-stg", client="cli-stg", niche="niche-stg", day="2026-09-01"):
    return {"order_id": oid, "order_num": oid, "order_date": day, "client_id": client,
            "niche": niche, "n3_id": n3, "n4_id": n4, "qty": qty}


def sl(sid, n4, qty, order_id=None, client="cli-stg", niche="niche-stg", day="2026-09-05"):
    return {"shipment_id": sid, "shipment_num": sid, "shipment_date": day,
            "order_id": order_id or "ord-" + sid, "client_id": client, "niche": niche,
            "n3_id": "n3-stg", "n4_id": n4, "qty": qty}


async def q(sql, **params):
    async with engine.begin() as conn:
        res = await conn.execute(text(sql), params)
        return res.all() if res.returns_rows else []


async def order_state(oid) -> dict[str, list[float]]:
    rows = await q("SELECT n4_id, qty FROM order_lines WHERE order_id = :o ORDER BY n4_id, qty", o=oid)
    out: dict[str, list[float]] = {}
    for r in rows:
        out.setdefault(r.n4_id, []).append(float(r.qty))
    return out


async def ship_state(sid) -> dict[str, list[float]]:
    rows = await q("SELECT n4_id, qty FROM shipment_lines WHERE shipment_id = :s ORDER BY n4_id, qty",
                   s=sid)
    out: dict[str, list[float]] = {}
    for r in rows:
        out.setdefault(r.n4_id, []).append(float(r.qty))
    return out


async def versions(oid) -> list[tuple[int, list[str]]]:
    rows = await q("SELECT version_seq, n4_id FROM order_line_versions WHERE order_id = :o "
                   "ORDER BY version_seq, n4_id", o=oid)
    out: dict[int, list[str]] = {}
    for r in rows:
        out.setdefault(r.version_seq, []).append(r.n4_id)
    return sorted(out.items())


# --- 1–2. Документ по одному товару в разных пачках --------------------------

async def test_order_split_by_product_is_assembled():
    """Сценарий АГAG-133619: 11 позиций приходят 11 запросами по одной."""
    for i in range(11):
        r = await repo.ingest_order_lines([ol("STG-133619", f"n4-{i:02d}", i + 1)])
        assert r["status"] == "staged" and r["batch_id"]
    assert await order_state("STG-133619") == {}          # до сборки боевые таблицы не тронуты

    summary = await staging.merge_staging("orders", "idle")
    state = await order_state("STG-133619")
    assert len(state) == 11 and sum(sum(v) for v in state.values()) == 66
    assert summary["batches"] == 11 and summary["documents"] == 1 and summary["lines"] == 11
    assert summary["single_product_share"] == 0.0
    assert len(await versions("STG-133619")) == 1         # одна версия на сборку, а не 11


async def test_shipment_split_by_product_is_assembled_in_any_order():
    """РТУ АГAG-052966: 6 товаров, пачки в перемешанном порядке; в одной пачке —
    две законно одинаковые строки одного товара (3,824 + 3,824)."""
    batches = [[sl("STG-052966", "n4-f", 2.75)],
               [sl("STG-052966", "n4-a", 1.364)],
               [sl("STG-052966", "n4-d", 3.824), sl("STG-052966", "n4-d", 3.824)],
               [sl("STG-052966", "n4-c", 1.785)],
               [sl("STG-052966", "n4-e", 4.699)],
               [sl("STG-052966", "n4-b", 1.491)]]
    for b in batches:
        await repo.ingest_shipment_lines(b)
    await staging.merge_staging("shipments", "idle")
    state = await ship_state("STG-052966")
    assert sorted(state) == ["n4-a", "n4-b", "n4-c", "n4-d", "n4-e", "n4-f"]
    assert state["n4-d"] == [3.824, 3.824]                 # кратность внутри пачки сохранена
    assert round(sum(sum(v) for v in state.values()), 3) == 19.737


# --- 3–7. Повторы, правка количества, кратность ---------------------------------

async def test_retry_of_pending_batch_is_not_written_twice():
    batch = [ol("STG-RETRY", "n4-a", 5), ol("STG-RETRY", "n4-b", 1)]
    first = await repo.ingest_order_lines(batch)
    second = await repo.ingest_order_lines(list(reversed(batch)))    # тот же состав, другой порядок
    assert first["status"] == "staged"
    assert second["status"] == "duplicate" and second["duplicate_of"] == first["batch_id"]
    assert second["accepted"] == 2                                   # 1С не увидит «0 принято»
    await staging.merge_staging("orders", "idle")
    assert await order_state("STG-RETRY") == {"n4-a": [5.0], "n4-b": [1.0]}


async def test_concurrent_retries_create_one_batch():
    batch = [ol("STG-RACE", "n4-a", 2)]
    res = await asyncio.gather(*(repo.ingest_order_lines(batch) for _ in range(5)))
    assert sorted(r["status"] for r in res).count("staged") == 1
    (n,), = await q("SELECT count(*) FROM ingest_batches WHERE merged_at IS NULL")
    assert n == 1


async def test_retry_after_merge_does_not_change_document():
    batch = [ol("STG-LATE-RETRY", "n4-a", 4)]
    await repo.ingest_order_lines(batch)
    await staging.merge_staging("orders", "idle")
    again = await repo.ingest_order_lines(batch)                     # ретрай после сборки
    assert again["status"] == "staged"
    await staging.merge_staging("orders", "idle")
    assert await order_state("STG-LATE-RETRY") == {"n4-a": [4.0]}
    assert len(await versions("STG-LATE-RETRY")) == 1                # состав не менялся → без версии


async def test_same_product_resent_with_new_qty_takes_last():
    """Документ пришёл двумя проходами в одной загрузке, между ними правка qty."""
    await repo.ingest_order_lines([ol("STG-QTY", "n4-a", 5)])
    await repo.ingest_order_lines([ol("STG-QTY", "n4-b", 1)])
    await repo.ingest_order_lines([ol("STG-QTY", "n4-a", 7)])        # второй проход: A=7
    await repo.ingest_order_lines([ol("STG-QTY", "n4-b", 1), ol("STG-QTY", "n4-c", 2)])
    await staging.merge_staging("orders", "idle")
    assert await order_state("STG-QTY") == {"n4-a": [7.0], "n4-b": [1.0], "n4-c": [2.0]}


async def test_page_sent_twice_among_others_is_not_doubled():
    page = [ol("STG-PAGE-1", "n4-a", 1), ol("STG-PAGE-2", "n4-a", 2)]
    await repo.ingest_order_lines(page)
    await repo.ingest_order_lines([ol("STG-PAGE-1", "n4-b", 3)])
    await repo.ingest_order_lines(page + [ol("STG-PAGE-2", "n4-b", 4)])   # страница повторно + новое
    await staging.merge_staging("orders", "idle")
    assert await order_state("STG-PAGE-1") == {"n4-a": [1.0], "n4-b": [3.0]}
    assert await order_state("STG-PAGE-2") == {"n4-a": [2.0], "n4-b": [4.0]}


# --- 8–9. Правило сужения -----------------------------------------------------

async def _two_loads_with_missing_position(oid):
    await repo.ingest_order_lines([ol(oid, "n4-a", 1), ol(oid, "n4-b", 2)])
    await staging.merge_staging("orders", "idle")
    await repo.ingest_order_lines([ol(oid, "n4-a", 1)])              # B в новой загрузке нет


async def test_missing_position_is_kept_by_default():
    await _two_loads_with_missing_position("STG-KEEP")
    summary = await staging.merge_staging("orders", "idle")
    assert await order_state("STG-KEEP") == {"n4-a": [1.0], "n4-b": [2.0]}
    assert summary["shrink_mode"] == "keep"
    assert summary["positions_kept"] == 1 and summary["documents_shrunk"] == 0


async def test_missing_position_is_removed_in_replace_mode(monkeypatch):
    await _two_loads_with_missing_position("STG-REPLACE")
    monkeypatch.setattr(settings, "ingest_shrink_mode", "replace")
    summary = await staging.merge_staging("orders", "idle")
    assert await order_state("STG-REPLACE") == {"n4-a": [1.0]}
    assert summary["documents_shrunk"] == 1 and summary["positions_kept"] == 0


async def test_replace_on_commit_only_for_commit_merges(monkeypatch):
    monkeypatch.setattr(settings, "ingest_shrink_mode", "replace_on_commit")
    await _two_loads_with_missing_position("STG-ROC")
    assert (await staging.merge_staging("orders", "idle"))["shrink_mode"] == "keep"
    assert await order_state("STG-ROC") == {"n4-a": [1.0], "n4-b": [2.0]}
    await repo.ingest_order_lines([ol("STG-ROC", "n4-a", 1)])
    assert (await staging.merge_staging("orders", "commit"))["shrink_mode"] == "replace"
    assert await order_state("STG-ROC") == {"n4-a": [1.0]}


async def test_qty_decrease_of_sent_product_is_applied_and_counted():
    await repo.ingest_shipment_lines([sl("STG-DEC", "n4-a", 5), sl("STG-DEC", "n4-a", 3)])
    await staging.merge_staging("shipments", "idle")
    await repo.ingest_shipment_lines([sl("STG-DEC", "n4-a", 5)])     # одна строка из двух
    summary = await staging.merge_staging("shipments", "idle")
    assert await ship_state("STG-DEC") == {"n4-a": [5.0]}
    assert summary["documents_qty_decreased"] == 1


# --- 10. deleted --------------------------------------------------------------

async def test_deleted_resets_document_and_disables_keep():
    await repo.ingest_order_lines([ol("STG-DEL", "n4-old", 9)])
    await staging.merge_staging("orders", "idle")
    await repo.ingest_order_lines([ol("STG-DEL", "n4-before", 1)])
    await repo.ingest_order_lines([{**ol("STG-DEL", "n4-x", 0), "deleted": True}])
    await repo.ingest_order_lines([ol("STG-DEL", "n4-after", 2)])
    await staging.merge_staging("orders", "idle")
    assert await order_state("STG-DEL") == {"n4-after": [2.0]}       # n4-old не сохранён keep'ом

    await repo.ingest_order_lines([{**ol("STG-DEL", "n4-x", 0), "deleted": True}])
    await staging.merge_staging("orders", "idle")
    assert await order_state("STG-DEL") == {}


# --- 11. Поздно зафиксированная старая пачка ----------------------------------

@pytest.mark.parametrize("shrink", ["keep", "replace"])
async def test_late_committed_older_batch_does_not_override_newer(monkeypatch, shrink):
    """Устаревшая пачка не перекрывает новые строки и не сужает документ ни в каком режиме."""
    monkeypatch.setattr(settings, "ingest_shrink_mode", shrink)
    old = await repo.ingest_order_lines([ol("STG-ORDER", "n4-a", 1)])
    new = await repo.ingest_order_lines([ol("STG-ORDER", "n4-a", 2), ol("STG-ORDER", "n4-b", 1)])
    assert old["batch_id"] < new["batch_id"]
    # Старая пачка «ещё не видна» сборке (транзакция зафиксировалась позже).
    await q("UPDATE ingest_batches SET merged_at = now() WHERE batch_id = :b", b=old["batch_id"])
    await staging.merge_staging("orders", "idle")
    await q("UPDATE ingest_batches SET merged_at = NULL WHERE batch_id = :b", b=old["batch_id"])
    await staging.merge_staging("orders", "idle")
    assert await order_state("STG-ORDER") == {"n4-a": [2.0], "n4-b": [1.0]}


# --- 12. Триггеры ----------------------------------------------------------------

async def _newest(kind):
    (ts,), = await q("SELECT max(received_at) FROM ingest_batches WHERE kind = :k "
                     "AND merged_at IS NULL", k=kind)
    return ts


async def test_idle_trigger_per_kind():
    await repo.ingest_order_lines([ol("STG-IDLE", "n4-a", 1)])
    await repo.ingest_shipment_lines([sl("STG-IDLE-S", "n4-a", 1)])
    t0 = await _newest("orders")
    assert not await staging.merge_tick(now=t0 + timedelta(minutes=10))   # пауза мала
    # Отгрузки «пришли позже»: сдвигаем их время, заказы простаивают дольше порога.
    await q("UPDATE ingest_batches SET received_at = received_at + interval '50 minutes' "
            "WHERE kind = 'shipments'")
    assert await staging.merge_tick(now=t0 + timedelta(minutes=61))
    assert await order_state("STG-IDLE") == {"n4-a": [1.0]}
    assert await ship_state("STG-IDLE-S") == {}                            # свой порог не наступил
    (trig,), = await q("SELECT trigger FROM ingest_merges WHERE kind = 'orders'")
    assert trig == "idle"


async def test_max_delay_trigger_under_continuous_stream():
    await repo.ingest_order_lines([ol("STG-MAXD", "n4-a", 1)])
    await q("UPDATE ingest_batches SET received_at = received_at - interval '25 hours'")
    await repo.ingest_order_lines([ol("STG-MAXD", "n4-b", 1)])            # свежая пачка — простоя нет
    assert await staging.merge_tick()
    (trig,), = await q("SELECT trigger FROM ingest_merges")
    assert trig == "max_delay"
    assert await order_state("STG-MAXD") == {"n4-a": [1.0], "n4-b": [1.0]}


async def test_commit_does_not_merge_by_default_and_merges_when_enabled(monkeypatch):
    await repo.ingest_order_lines([ol("STG-COMMIT", "n4-a", 1)])
    res = await staging.request_commit("orders")
    assert res["merge_ids"] == {}
    assert not await staging.merge_tick()                                # 1С зовёт commit на каждую пачку
    monkeypatch.setattr(settings, "ingest_merge_on_commit", True)
    res = await staging.request_commit("orders")
    assert await staging.merge_tick()
    assert await order_state("STG-COMMIT") == {"n4-a": [1.0]}
    m = await staging.get_merge(res["merge_ids"]["orders"])
    assert m["trigger"] == "commit" and m["batches"] == 1 and m["finished_at"]


async def test_api_staged_ingest_commit_stats_and_merge_status():
    async with httpx.AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as c:
        r = await c.post("/api/ingest/order-lines", json={"items": [ol("STG-API", "n4-a", 1)]})
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "staged" and body["batch_id"] and body["accepted"] == 1
        dup = (await c.post("/api/ingest/order-lines",
                            json={"items": [ol("STG-API", "n4-a", 1)]})).json()
        assert dup["status"] == "duplicate" and dup["duplicate_of"] == body["batch_id"]
        empty = (await c.post("/api/ingest/shipments", json={"items": []})).json()
        assert empty["status"] == "empty" and empty["accepted"] == 0

        cm = await c.post("/api/ingest/commit")
        assert cm.status_code == 202 and cm.json()["status"] == "accepted"
        stats = (await c.get("/api/ingest/stats")).json()
        assert stats["ingest_mode"] == "staged" and stats["pending"]["orders"]["batches"] == 1

        summary = await staging.merge_staging("orders", "manual")
        m = await c.get(f"/api/ingest/merges/{summary['merge_id']}")
        assert m.status_code == 200 and m.json()["documents"] == 1
        assert (await c.get("/api/ingest/merges/999999")).status_code == 404


def _app():
    from backend.app.main import app
    return app


# --- 13. Версии и метрики эффекта ----------------------------------------------

async def test_version_ingested_at_is_first_batch_and_new_order_effect_not_faked():
    """Заказ пришёл ДО рекомендации, собран ПОСЛЕ — не «новый заказ» в эффекте."""
    from datetime import date

    today = date.today().isoformat()
    await repo.ingest_order_lines([ol("STG-EFF-SRC", "n4-base", 1, client="cli-stg-eff",
                                      niche="niche-stg-eff", day=today)])
    await staging.merge_staging("orders", "idle")
    await repo.ingest_order_lines([ol("STG-EFF-TGT", "n4-Z", 3, client="cli-stg-eff",
                                      niche="niche-stg-eff", day=today)])
    (received,), = await q("SELECT max(received_at) FROM ingest_batches WHERE merged_at IS NULL")
    await asyncio.sleep(0.05)
    await repo.log_recommendations("cli-stg-eff", "STG-EFF-SRC",
                                   [{"n3_id": "n3-stg", "n4_id": "n4-Z", "priority": "high"}], [])
    await staging.merge_staging("orders", "idle")                       # собран после рекомендации
    await repo.ingest_shipment_lines([sl("STG-EFF-SH", "n4-Z", 3, order_id="STG-EFF-TGT",
                                         client="cli-stg-eff", niche="niche-stg-eff", day=today)])
    await staging.merge_staging("shipments", "idle")
    await repo.refresh_aggregates()

    (v_at,), = await q("SELECT min(ingested_at) FROM order_line_versions WHERE order_id = 'STG-EFF-TGT'")
    assert v_at == received                                             # время первой пачки, не сборки
    rows, _ = await repo.get_recommendation_effect_n4(niche="niche-stg-eff")
    assert not [r for r in rows if r["target_order_id"] == "STG-EFF-TGT"]


async def test_order_split_across_auto_merges_coalesces_versions():
    """Заказ разрезан границей автосборки: вторая половина не выглядит «добавленной»."""
    await repo.ingest_order_lines([ol("STG-COAL", "n4-a", 1)])
    await staging.merge_staging("orders", "idle")
    await repo.ingest_order_lines([ol("STG-COAL", "n4-b", 2)])
    await staging.merge_staging("orders", "idle")
    assert [v for _, v in await versions("STG-COAL")] == [["n4-a", "n4-b"]]
    await repo.refresh_aggregates()
    (is_new,), = await q("SELECT is_new_position FROM effect_funnel WHERE order_id = 'STG-COAL'")
    assert is_new == 0


async def test_real_change_after_auto_merge_creates_new_version():
    await repo.ingest_order_lines([ol("STG-CHG", "n4-a", 1)])
    await staging.merge_staging("orders", "idle")
    await repo.ingest_order_lines([ol("STG-CHG", "n4-a", 5)])           # изменено qty — не надмножество
    await staging.merge_staging("orders", "idle")
    assert len(await versions("STG-CHG")) == 2


async def test_no_coalescing_outside_window_or_for_manual_merge(monkeypatch):
    await repo.ingest_order_lines([ol("STG-NOCOAL", "n4-a", 1)])
    await staging.merge_staging("orders", "manual")                      # не автосборка
    await repo.ingest_order_lines([ol("STG-NOCOAL", "n4-b", 1)])
    await staging.merge_staging("orders", "idle")
    assert len(await versions("STG-NOCOAL")) == 2

    monkeypatch.setattr(settings, "ingest_version_coalesce_minutes", 0)
    await repo.ingest_order_lines([ol("STG-NOCOAL2", "n4-a", 1)])
    await staging.merge_staging("orders", "idle")
    await repo.ingest_order_lines([ol("STG-NOCOAL2", "n4-b", 1)])
    await staging.merge_staging("orders", "idle")
    assert len(await versions("STG-NOCOAL2")) == 2


# --- 14–15. Ошибки и конкурентность ---------------------------------------------

async def test_failed_merge_rolls_back(monkeypatch):
    await repo.ingest_order_lines([ol("STG-FAIL", "n4-a", 1)])

    async def boom(*a, **kw):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(staging, "_write_versions", boom)
    with pytest.raises(RuntimeError):
        await staging.merge_staging("orders", "idle")
    assert await order_state("STG-FAIL") == {}
    (pending,), = await q("SELECT count(*) FROM ingest_batches WHERE merged_at IS NULL")
    (err,), = await q("SELECT error FROM ingest_merges ORDER BY merge_id DESC LIMIT 1")
    assert pending == 1 and "synthetic failure" in err
    monkeypatch.undo()
    monkeypatch.setattr(settings, "ingest_mode", "staged")
    await staging.merge_staging("orders", "idle")                        # повтор на следующем тике
    assert await order_state("STG-FAIL") == {"n4-a": [1.0]}


async def test_concurrent_merges_and_refresh_do_not_deadlock():
    for i in range(5):
        await repo.ingest_order_lines([ol("STG-CONC", f"n4-{i}", 1)])
    res = await asyncio.wait_for(asyncio.gather(
        staging.merge_staging("orders", "idle"),
        staging.merge_staging("orders", "idle"),
        repo.refresh_aggregates(),
    ), timeout=60)
    assert sum(1 for r in res[:2] if r) == 1                             # собрала ровно одна
    assert len(await order_state("STG-CONC")) == 5


# --- Сид и режим direct ----------------------------------------------------------

async def test_seed_works_in_staged_mode():
    from backend.sync.seed_mock import seed

    await seed()
    assert await repo.get_client_niche("clnt0000-0000-0000-0000-000000000000")
    (pending,), = await q("SELECT count(*) FROM ingest_batches WHERE merged_at IS NULL")
    assert pending == 0


async def test_direct_mode_reports_shrinking_documents(monkeypatch, caplog):
    monkeypatch.setattr(settings, "ingest_mode", "direct")
    await repo.ingest_shipment_lines([sl("STG-DIRECT", "n4-a", 1), sl("STG-DIRECT", "n4-b", 1)])
    with caplog.at_level("WARNING", logger="ingest"):
        r = await repo.ingest_shipment_lines([sl("STG-DIRECT", "n4-b", 1)])
    assert r["status"] == "applied" and r["documents_shrunk"] == 1
    assert "fewer products" in caplog.text
    assert await ship_state("STG-DIRECT") == {"n4-b": [1.0]}            # direct: прежнее поведение


# --- Сквозной сценарий: выгрузка «как у 1С на стенде» ----------------------------

def _e2e_rows():
    """Моковая история (≈1000 строк, ≈480 заказов, до 4 товаров в заказе) с
    собственными идентификаторами, чтобы не задеть данные других тестов."""
    from backend.sync.mock_source import generate_rows, orders_to_shipments

    def remap(r):
        r = dict(r)
        for key in ("order_id", "shipment_id", "client_id", "order_num", "shipment_num"):
            if key in r:
                r[key] = "E2E-" + r[key]
        for key in ("order_date", "shipment_date"):
            if key in r:
                r[key] = r[key].isoformat()
        return r

    orders = generate_rows()
    return [remap(r) for r in orders], [remap(r) for r in orders_to_shipments(orders)]


def _pages_like_1c(rows, size=25):
    """Как выгрузка 1С: строки отсортированы по номенклатуре и нарезаны на страницы,
    поэтому строки одного документа разнесены по разным запросам."""
    rows = sorted(rows, key=lambda r: (r["n4_id"], r.get("shipment_id") or r["order_id"]))
    return [rows[i:i + size] for i in range(0, len(rows), size)]


def _truth(rows, key):
    out: dict[str, list] = {}
    for r in rows:
        out.setdefault(r[key], []).append((r["n4_id"], round(float(r["qty"]), 3)))
    return {k: sorted(v) for k, v in out.items()}


async def _db_state(table, key):
    rows = await q(f"SELECT {key} AS k, n4_id, qty FROM {table} WHERE {key} LIKE 'E2E-%'")
    out: dict[str, list] = {}
    for r in rows:
        out.setdefault(r.k, []).append((r.n4_id, round(float(r.qty), 3)))
    return {k: sorted(v) for k, v in out.items()}


async def _push_like_1c(c, orders, ships):
    for page in _pages_like_1c(orders):
        assert (await c.post("/api/ingest/order-lines", json={"items": page})).status_code == 200
        assert (await c.post("/api/ingest/commit")).status_code in (200, 202)   # commit на каждую пачку
    for page in _pages_like_1c(ships):
        assert (await c.post("/api/ingest/shipments", json={"items": page})).status_code == 200
        assert (await c.post("/api/ingest/commit")).status_code in (200, 202)


async def test_e2e_feed_like_1c_direct_loses_lines_staged_assembles(monkeypatch):
    orders, ships = _e2e_rows()
    truth_o, truth_s = _truth(orders, "order_id"), _truth(ships, "shipment_id")
    multi = [k for k, v in truth_o.items() if len({n4 for n4, _ in v}) > 1]
    assert len(multi) > 100                                     # в синтетике много многотоварных заказов

    await q("DELETE FROM order_lines WHERE order_id LIKE 'E2E-%'")
    await q("DELETE FROM shipment_lines WHERE shipment_id LIKE 'E2E-%'")
    await q("DELETE FROM order_line_versions WHERE order_id LIKE 'E2E-%'")

    async with httpx.AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as c:
        # Прежний режим: воспроизводим дефект стенда.
        monkeypatch.setattr(settings, "ingest_mode", "direct")
        await _push_like_1c(c, orders, ships)
        db_o = await _db_state("order_lines", "order_id")
        broken = [k for k in multi if db_o.get(k) != truth_o[k]]
        assert len(broken) == len(multi)                        # каждый многотоварный заказ усечён
        assert all(len({n4 for n4, _ in db_o[k]}) == 1 for k in multi)

        # Новый режим: та же выгрузка, сборка по паузе.
        monkeypatch.setattr(settings, "ingest_mode", "staged")
        await _push_like_1c(c, orders, ships)
        assert await _db_state("order_lines", "order_id") == db_o    # до сборки боевые таблицы не тронуты
        o = await staging.merge_staging("orders", "idle")
        s = await staging.merge_staging("shipments", "idle")
        assert o["documents"] == len(truth_o) and s["documents"] == len(truth_s)

    assert await _db_state("order_lines", "order_id") == truth_o
    assert await _db_state("shipment_lines", "shipment_id") == truth_s

    # Тоннаж клиента в /analyze совпадает с эталоном.
    await repo.refresh_aggregates()
    client = orders[0]["client_id"]
    truth_qty = round(sum(float(r["qty"]) for r in ships if r["client_id"] == client), 3)
    summary = await repo.get_client_summary(client, *_full_period())
    assert summary["shipped_qty"] == truth_qty


def _full_period():
    from datetime import date
    return date(2000, 1, 1), date(2100, 1, 1)


# --- Ревью 27.10.2026 -------------------------------------------------------------

async def test_value_returning_to_earlier_one_is_not_lost():
    """S1: в одной загрузке qty 5 → 7 → 5. Третья пачка совпадает с первой, но не
    с последней — это не ретрай, итог 5 (а не 7)."""
    for qty in (5, 7, 5):
        r = await repo.ingest_order_lines([ol("STG-S1", "n4-a", qty)])
        assert r["status"] == "staged"
    await staging.merge_staging("orders", "idle")
    assert await order_state("STG-S1") == {"n4-a": [5.0]}


async def test_coalesce_window_counts_from_merge_time():
    """S2: первая пачка пришла 5 ч назад, собрана только что; дослан товар —
    версии сливаются (окно от времени сборки, не от прихода пачки)."""
    await repo.ingest_order_lines([ol("STG-S2", "n4-a", 1)])
    await q("UPDATE ingest_batches SET received_at = received_at - interval '5 hours'")
    await staging.merge_staging("orders", "idle")
    await repo.ingest_order_lines([ol("STG-S2", "n4-b", 1)])
    await staging.merge_staging("orders", "idle")
    assert [v for _, v in await versions("STG-S2")] == [["n4-a", "n4-b"]]


async def test_stale_staged_batches_do_not_overwrite_direct_writes(monkeypatch, caplog):
    """S3: staged-пачка a=1 не собрана; режим переключили в direct, 1С записала a=9, b=2;
    вернули staged — старая пачка отброшена, свежие данные целы."""
    await repo.ingest_shipment_lines([sl("STG-S3", "n4-a", 1)])
    monkeypatch.setattr(settings, "ingest_mode", "direct")
    await repo.ingest_shipment_lines([sl("STG-S3", "n4-a", 9), sl("STG-S3", "n4-b", 2)])
    st = await staging.get_stats()
    assert st["warnings"] and st["pending"]["shipments"]["batches"] == 1
    monkeypatch.setattr(settings, "ingest_mode", "staged")
    with caplog.at_level("ERROR", logger="ingest.staging"):
        assert await staging.merge_staging("shipments", "idle") is None
    assert "discarded 1 staged batch" in caplog.text
    assert await ship_state("STG-S3") == {"n4-a": [9.0], "n4-b": [2.0]}
    (pending,), = await q("SELECT count(*) FROM ingest_batches WHERE merged_at IS NULL")
    assert pending == 0


async def test_failed_kind_does_not_block_other_kind(monkeypatch):
    """S7: сборка заказов падает — отгрузки всё равно собираются."""
    await repo.ingest_order_lines([ol("STG-S7", "n4-a", 1)])
    await repo.ingest_shipment_lines([sl("STG-S7-S", "n4-a", 1)])
    real = staging._merge

    async def failing(k, trigger, merge_id):
        if k.name == "orders":
            raise RuntimeError("synthetic orders failure")
        return await real(k, trigger, merge_id)

    monkeypatch.setattr(staging, "_merge", failing)
    monkeypatch.setattr(settings, "ingest_merge_idle_minutes", 0)
    assert await staging.merge_tick()
    assert await ship_state("STG-S7-S") == {"n4-a": [1.0]}
    assert await order_state("STG-S7") == {}
