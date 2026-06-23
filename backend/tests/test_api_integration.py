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
            json={"client_guid": KNOWN_CLIENT, "lines": [{"n3": "x", "qty": 1}]},
        )
    assert r.status_code == 200
    body = r.json()
    assert {"analysis1_forgotten", "analysis2_niche", "niche"} <= set(body)


async def test_analyze_rejects_empty_order():
    async with _client() as c:
        r = await c.post("/api/analyze", json={"client_guid": KNOWN_CLIENT, "lines": []})
    assert r.status_code == 400


async def test_ingest_then_analyze_end_to_end():
    """Push an order via ingest, refresh, then analyze resolves item_guid → n3 and
    flags the omitted subgroup as 'forgotten'."""
    client = "cli-test-0001"
    order = {
        "items": [
            {
                "order_guid": "ord-test-0001", "order_num": "T-1",
                "order_date": "2026-06-01", "client_guid": client,
                "client_name": "ТестКлиент", "niche": "ТестНиша",
                "n3": "ТЕСТ.A", "n4": "Товар A", "item_guid": "itm-A", "qty": 3,
            },
            {
                "order_guid": "ord-test-0001", "order_num": "T-1",
                "order_date": "2026-06-01", "client_guid": client,
                "client_name": "ТестКлиент", "niche": "ТестНиша",
                "n3": "ТЕСТ.B", "n4": "Товар B", "item_guid": "itm-B", "qty": 4,
            },
        ],
    }
    async with _client() as c:
        ing = await c.post("/api/ingest/order-lines", json=order)
        assert ing.status_code == 200
        assert ing.json()["accepted"] == 2

        cm = await c.post("/api/ingest/commit")
        assert cm.status_code == 200 and cm.json()["rows"] > 0

        # draft order has only subgroup A → B should surface as forgotten
        an = await c.post(
            "/api/analyze",
            json={"client_guid": client, "lines": [{"item_guid": "itm-A", "qty": 3}]},
        )
    assert an.status_code == 200
    body = an.json()
    assert body["niche"] == "ТестНиша"
    assert any(r["n3"] == "ТЕСТ.B" for r in body["analysis1_forgotten"])
