"""API tests via ASGI transport. Needs a migrated+reachable DB (seeded here).

Auth is left disabled (no AUTH_JWT_SECRET), so endpoints are reachable without a
token — token issuance itself is covered in test_auth.py.
"""
import os

import httpx
import pytest
from httpx import ASGITransport

from backend.app.main import app
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


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_health():
    async with _client() as c:
        r = await c.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


async def test_analyze_contract():
    async with _client() as c:
        r = await c.post(
            "/api/analyze",
            json={"client_id": KNOWN_CLIENT, "lines": [{"n3_id": "x", "qty": 1}]},
        )
    assert r.status_code == 200
    assert {"analysis1_forgotten", "analysis2_niche", "niche"} <= set(r.json())


async def test_analyze_rejects_empty_order():
    async with _client() as c:
        r = await c.post("/api/analyze", json={"client_id": KNOWN_CLIENT, "lines": []})
    assert r.status_code == 400


async def test_ingest_then_analyze_end_to_end():
    """Push an order via ingest, refresh, then analyze resolves n4_id → n3_id and
    flags the omitted subgroup as 'forgotten'."""
    def line(n3, n4):
        return {
            "order_num": "T-1", "order_date": "2026-06-01",
            "client_id": "cli-test-0001", "client_name": "ТестКлиент", "niche": "ТестНиша",
            "n3_id": n3, "n4_id": n4, "qty": 3,
        }
    async with _client() as c:
        ing = await c.post("/api/ingest/order-lines",
                           json={"items": [line("n3-A", "n4-A"), line("n3-B", "n4-B")]})
        assert ing.status_code == 200 and ing.json()["accepted"] == 2

        cm = await c.post("/api/ingest/commit")
        assert cm.status_code == 200 and cm.json()["rows"] > 0

        # draft order has only subgroup n3-A → n3-B should surface as forgotten
        an = await c.post(
            "/api/analyze",
            json={"client_id": "cli-test-0001", "lines": [{"n4_id": "n4-A", "qty": 3}]},
        )
    assert an.status_code == 200
    body = an.json()
    assert body["niche"] == "ТестНиша"
    assert any(r["n3_id"] == "n3-B" for r in body["analysis1_forgotten"])
