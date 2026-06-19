"""Integration tests against a real PostgreSQL.

Skipped automatically unless a DB is reachable. Requires migrations applied
(``alembic -c backend/alembic.ini upgrade head``) on the target database.

    pytest -m integration
"""
import os

import pytest

from backend.app import repository as repo, services
from backend.sync.seed_mock import seed

pytestmark = pytest.mark.integration

_DB = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    if not _DB:
        pytest.skip("no DATABASE_URL / TEST_DATABASE_URL set")
    try:
        await seed()
    except Exception as e:  # pragma: no cover - environment dependent
        pytest.skip(f"DB not reachable / not migrated: {e}")
    yield


async def test_clients_loaded():
    clients = await repo.get_clients()
    assert len(clients) > 0
    assert all({"client_guid", "client_name", "niche"} <= set(c) for c in clients)


async def test_client_profile_and_total_orders():
    client = (await repo.get_clients())[0]
    guid = client["client_guid"]
    total = await repo.get_client_total_orders(guid)
    assert total > 0
    profile = await repo.get_client_profile_n4(guid)
    assert profile
    assert all(0 <= r["frequency_pct"] <= 100 for r in profile)


async def test_products_catalog_nonempty():
    n3s = await repo.get_products_n3()
    assert n3s
    n4s = await repo.get_products_n4(n3s[0])
    assert n4s and "item_guid" in n4s[0]


async def test_analyze_end_to_end():
    client = (await repo.get_clients())[0]
    result = await services.analyze(client["client_guid"], order_lines=[])
    assert "analysis1_forgotten" in result
    assert "analysis2_niche" in result
    # stock enrichment keys present
    for rec in result["analysis1_forgotten"]:
        assert "stock_on" in rec and "stock_in_transit" in rec
