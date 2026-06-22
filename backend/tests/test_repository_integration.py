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

# Deterministic guid of mock client #1 (mock_source seed=42).
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
    niche = await repo.get_client_niche(KNOWN_CLIENT)
    assert niche
    profile = await repo.get_client_profile_n4(KNOWN_CLIENT)
    assert profile
    assert all(0 <= r["frequency_pct"] <= 100 for r in profile)


async def test_n3_resolution_from_catalog():
    profile = await repo.get_client_profile_n4(KNOWN_CLIENT)
    item_guid = profile[0]["item_guid"]
    resolved = await repo.get_n3_for_items([item_guid])
    assert resolved.get(item_guid) == profile[0]["n3"]


async def test_analyze_end_to_end():
    result = await services.analyze(KNOWN_CLIENT, lines=[])
    assert "analysis1_forgotten" in result
    assert "analysis2_niche" in result
    for rec in result["analysis1_forgotten"]:
        assert "stock_on" in rec and "stock_in_transit" in rec
