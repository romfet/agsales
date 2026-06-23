"""Integration tests against a real PostgreSQL.

Skipped unless a DB is reachable. Requires migrations applied
(``alembic -c backend/alembic.ini upgrade head``).
"""
import os

import pytest

from backend.app import repository as repo, services
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
