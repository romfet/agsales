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
    profile = await repo.get_client_profile_n4(KNOWN_CLIENT)
    assert profile
    assert all(0 <= r["frequency_pct"] <= 100 for r in profile)


async def test_n4_to_n3_resolution_from_catalog():
    profile = await repo.get_client_profile_n4(KNOWN_CLIENT)
    n4_id = profile[0]["n4_id"]
    resolved = await repo.get_n3_for_n4([n4_id])
    assert resolved.get(n4_id) == profile[0]["n3_id"]


async def test_analyze_end_to_end():
    result = await services.analyze(KNOWN_CLIENT, lines=[])
    assert "analysis1_forgotten" in result
    assert "analysis2_niche" in result


async def test_client_profile_n3_is_one_row_per_subgroup():
    """Regression: two SKUs of one subgroup across two orders must yield ONE N3
    row with order_count = distinct orders (not one row per SKU / per line)."""
    def line(order, n4):
        return {"order_id": order, "order_num": order, "order_date": "2026-05-01",
                "client_id": "cli-n3-test", "client_name": "X", "niche": "НишаX",
                "n3_id": "n3-X", "n4_id": n4, "qty": 2}

    # order R-1: two SKUs of n3-X; order R-2: one SKU → subgroup in 2 distinct orders
    await repo.ingest_order_lines([line("R-1", "n4-1"), line("R-1", "n4-2"), line("R-2", "n4-1")])
    await repo.refresh_aggregates()

    rows = [r for r in await repo.get_client_profile_n3("cli-n3-test") if r["n3_id"] == "n3-X"]
    assert len(rows) == 1                    # one row per subgroup, not per SKU
    assert rows[0]["order_count"] == 2       # distinct orders, not 3 lines / 2 SKUs
    assert rows[0]["total_orders"] == 2
    assert rows[0]["frequency_pct"] == 100.0
    assert rows[0]["n4_id"] in {"n4-1", "n4-2"}   # representative SKU carried through


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

    async with engine.begin() as conn:
        cur = (await conn.execute(text(
            "SELECT count(*) FROM order_lines WHERE order_id='OV-1'"))).scalar()
        vers = (await conn.execute(text(
            "SELECT count(DISTINCT ingested_at) FROM order_line_versions WHERE order_id='OV-1'"))).scalar()
    assert cur == 3                       # текущий срез — последняя версия (A,B,C), не задвоился
    assert vers == 2                      # обе версии сохранены

    diff = {d["n3_id"]: d for d in await repo.get_order_diff("OV-1")}
    assert diff["n3-c"]["status"] == "added"
    assert diff["n3-c"]["qty_first"] == 0.0 and diff["n3-c"]["qty_last"] == 7.0
    assert diff["n3-a"]["status"] == "unchanged"
    assert diff["n3-b"]["status"] == "unchanged"
