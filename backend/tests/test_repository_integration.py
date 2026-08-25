"""Integration tests against a real PostgreSQL.

Skipped unless a DB is reachable. Requires the schema applied
(``python -m backend.db.schema``).
"""
import os
import pathlib

import pytest
from sqlalchemy import text

from backend.app import repository as repo, services
from backend.app.db.session import engine
from backend.sync.seed_mock import seed

pytestmark = pytest.mark.integration

_DB = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")

# Deterministic client_id of mock client #1 (mock_source seed=42).
KNOWN_CLIENT = "clnt0000-0000-0000-0000-000000000000"


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    if not _DB:
        pytest.skip("no DATABASE_URL / TEST_DATABASE_URL set")
    try:
        await seed()
    except Exception as e:  # pragma: no cover - environment dependent
        pytest.skip(f"DB not reachable / not migrated: {e}")
    yield


async def test_client_niche_and_profile():
    assert await repo.get_client_niche(KNOWN_CLIENT)
    profile = await repo.get_client_profile_n3(KNOWN_CLIENT)
    assert profile
    assert all(0 <= r["frequency_pct"] <= 100 for r in profile)


async def test_n4_to_n3_resolution_from_catalog():
    profile = await repo.get_client_profile_n3(KNOWN_CLIENT)
    n4_id = profile[0]["n4_id"]                   # представительный товар подгруппы
    resolved = await repo.get_n3_for_n4([n4_id])
    assert resolved.get(n4_id) == profile[0]["n3_id"]


async def test_analyze_end_to_end():
    result = await services.analyze(KNOWN_CLIENT, lines=[])
    assert "analysis1_forgotten" in result
    assert "analysis2_niche" in result


async def test_client_profile_n3_is_one_row_per_subgroup():
    """Regression: два SKU одной подгруппы в двух отгрузках → ОДНА N3-строка,
    order_count = число РАЗНЫХ отгрузок (не по SKU / не по строкам). Профили теперь
    строятся из shipment_lines, единица события — shipment_id."""
    def sline(shipment, n4):
        return {"shipment_id": shipment, "shipment_num": shipment, "shipment_date": "2026-05-01",
                "order_id": "ord-" + shipment, "client_id": "cli-n3-test", "niche": "НишаX",
                "n3_id": "n3-X", "n4_id": n4, "qty": 2}

    # S-1: два SKU n3-X; S-2: один SKU → подгруппа в 2 разных отгрузках
    await repo.ingest_shipment_lines([sline("S-1", "n4-1"), sline("S-1", "n4-2"), sline("S-2", "n4-1")])
    await repo.refresh_aggregates()

    rows = [r for r in await repo.get_client_profile_n3("cli-n3-test") if r["n3_id"] == "n3-X"]
    assert len(rows) == 1                    # одна строка на подгруппу, не по SKU
    assert rows[0]["order_count"] == 2       # разные отгрузки, не 3 строки / 2 SKU
    assert rows[0]["total_orders"] == 2
    assert rows[0]["frequency_pct"] == 100.0
    assert rows[0]["n4_id"] in {"n4-1", "n4-2"}   # представительный SKU


async def test_recommendation_marker_ingest_and_effect_view():
    """Маркер рекомендации: ingest сохраняет rec_source/kind/origin; витрина
    effect_metrics делит сценарии по origin (=order_num → №1, иначе → №2)."""
    def line(order, n3, n4, qty, rec=False, kind=None, origin=None):
        return {"order_id": order, "order_num": order, "order_date": "2026-07-01",
                "client_id": "cli-mark", "client_name": "M", "niche": "НишаM",
                "n3_id": n3, "n4_id": n4, "qty": qty,
                "rec_source": rec, "rec_kind": kind, "rec_origin_order": origin}

    await repo.ingest_order_lines([
        line("M-1", "n3-a", "n4-a", 5),                                         # обычная позиция
        line("M-1", "n3-b", "n4-b", 2, rec=True, kind="forgotten", origin="M-1"),  # сценарий №1
        line("M-2", "n3-c", "n4-c", 7, rec=True, kind="niche", origin="M-1"),      # сценарий №2
    ])

    view_sql = (pathlib.Path(__file__).resolve().parents[1] / "db" / "effect_metrics.sql").read_text(encoding="utf-8")
    async with engine.begin() as conn:
        stored = {
            (r.order_num, r.n3_id): (r.rec_source, r.rec_kind, r.rec_origin_order)
            for r in (await conn.execute(text(
                "SELECT order_num, n3_id, rec_source, rec_kind, rec_origin_order "
                "FROM order_lines WHERE client_id='cli-mark'"))).all()
        }
        await conn.exec_driver_sql(view_sql)   # прод-SQL витрины (CREATE OR REPLACE VIEW)
        eff = {
            r.scenario: (r.rec_kind, float(r.added_tonnage))
            for r in (await conn.execute(text(
                "SELECT scenario, rec_kind, added_tonnage FROM effect_metrics "
                "WHERE niche='НишаM'"))).all()
        }

    assert stored[("M-1", "n3-a")] == (False, None, None)                 # обычная — без маркера
    assert stored[("M-1", "n3-b")] == (True, "forgotten", "M-1")
    assert stored[("M-2", "n3-c")] == (True, "niche", "M-1")
    assert eff["Сценарий №1"] == ("forgotten", 2.0)                       # добавлено в тот же заказ
    assert eff["Сценарий №2"] == ("niche", 7.0)                           # новый заказ


async def test_order_versioning_and_diff():
    """Заказ версионируется по order_id: повторный ingest не затирает историю;
    order_lines = текущий срез, get_order_diff показывает добавленное."""
    def line(n3, n4, qty):
        return {"order_id": "OV-1", "order_num": "OV-1", "order_date": "2026-08-01",
                "client_id": "cli-ver", "niche": "НишаV",
                "n3_id": n3, "n4_id": n4, "qty": qty}

    await repo.ingest_order_lines([line("n3-a", "n4-a", 4), line("n3-b", "n4-b", 3)])          # версия 1: A,B
    await repo.ingest_order_lines([line("n3-a", "n4-a", 4), line("n3-b", "n4-b", 3),
                                   line("n3-c", "n4-c", 7)])                                    # версия 2: +C
    await repo.ingest_order_lines([line("n3-a", "n4-a", 4), line("n3-b", "n4-b", 3),
                                   line("n3-c", "n4-c", 7)])                                    # тот же состав → дедуп

    async with engine.begin() as conn:
        cur = (await conn.execute(text(
            "SELECT count(*) FROM order_lines WHERE order_id='OV-1'"))).scalar()
        vers = (await conn.execute(text(
            "SELECT count(DISTINCT version_seq) FROM order_line_versions WHERE order_id='OV-1'"))).scalar()
    assert cur == 3                       # текущий срез — последняя версия (A,B,C), не задвоился
    assert vers == 2                      # дедуп: неизменная 3-я загрузка НЕ создала версию

    diff = {d["n3_id"]: d for d in await repo.get_order_diff("OV-1")}
    assert diff["n3-c"]["status"] == "added"
    assert diff["n3-c"]["qty_first"] == 0.0 and diff["n3-c"]["qty_last"] == 7.0
    assert diff["n3-a"]["status"] == "unchanged"
    assert diff["n3-b"]["status"] == "unchanged"


async def test_ingest_shipments_upsert_and_m2m():
    """Отгрузки: upsert по shipment_id; заказ↔отгрузка many-to-many."""
    def sline(sid, order_id, n3, n4, qty):
        return {"shipment_id": sid, "shipment_num": "SH-" + sid, "shipment_date": "2026-08-10",
                "order_id": order_id, "client_id": "cli-sh", "niche": "НишаS",
                "n3_id": n3, "n4_id": n4, "qty": qty}

    # одна отгрузка S1 покрывает ДВА заказа (m2m: shipment → many orders)
    await repo.ingest_shipment_lines([sline("S1", "ORD-A", "n3-a", "n4-a", 5),
                                      sline("S1", "ORD-B", "n3-b", "n4-b", 3)])
    # заказ ORD-A встречается и во ВТОРОЙ отгрузке (m2m: order → many shipments)
    await repo.ingest_shipment_lines([sline("S2", "ORD-A", "n3-a", "n4-a", 2)])
    # upsert: перезалив S1 с изменённым qty у ORD-A (было 5 → стало 9)
    await repo.ingest_shipment_lines([sline("S1", "ORD-A", "n3-a", "n4-a", 9),
                                      sline("S1", "ORD-B", "n3-b", "n4-b", 3)])

    async with engine.begin() as conn:
        rows = (await conn.execute(text(
            "SELECT shipment_id, order_id, qty FROM shipment_lines WHERE client_id='cli-sh'"))).all()
    got = {(r.shipment_id, r.order_id): float(r.qty) for r in rows}
    assert got[("S1", "ORD-A")] == 9.0     # S1 перезаписалась (upsert по shipment_id)
    assert got[("S1", "ORD-B")] == 3.0
    assert got[("S2", "ORD-A")] == 2.0     # тот же заказ в другой отгрузке
    assert len(rows) == 3


async def test_products_catalog_covers_ordered_not_shipped():
    """Ревью #1: каталог products (n4→n3) покрывает товары из ЗАКАЗОВ, даже если
    они ещё не отгружены — иначе позиция черновика не резолвилась бы."""
    await repo.ingest_order_lines([{
        "order_id": "ORD-P", "order_num": "ORD-P", "order_date": "2026-06-01",
        "client_id": "cli-p", "niche": "НишаP", "n3_id": "n3-ord", "n4_id": "n4-ordonly", "qty": 1,
    }])
    await repo.refresh_aggregates()
    resolved = await repo.get_n3_for_n4(["n4-ordonly"])
    assert resolved.get("n4-ordonly") == "n3-ord"   # резолв из заказов, не только отгрузок


async def test_ingest_accepts_datetime_string_date():
    """Ревью #2: дата как ISO-datetime ('...T..') не роняет ingest — берём дату."""
    from datetime import date as _date

    await repo.ingest_order_lines([{
        "order_id": "ORD-DT", "order_num": "ORD-DT", "order_date": "2026-06-15T08:30:00",
        "client_id": "cli-dt", "niche": "НишаDT", "n3_id": "n3-dt", "n4_id": "n4-dt", "qty": 1,
    }])
    await repo.ingest_shipment_lines([{
        "shipment_id": "SH-DT", "shipment_num": "SH-DT", "shipment_date": "2026-06-16T00:00:00",
        "order_id": "ORD-DT", "client_id": "cli-dt", "niche": "НишаDT",
        "n3_id": "n3-dt", "n4_id": "n4-dt", "qty": 1,
    }])
    async with engine.begin() as conn:
        od = (await conn.execute(text("SELECT order_date FROM order_lines WHERE order_id='ORD-DT'"))).scalar()
        sd = (await conn.execute(text("SELECT shipment_date FROM shipment_lines WHERE shipment_id='SH-DT'"))).scalar()
    assert od == _date(2026, 6, 15)
    assert sd == _date(2026, 6, 16)
