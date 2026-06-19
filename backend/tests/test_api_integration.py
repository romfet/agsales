"""API tests via ASGI transport. Needs a migrated+reachable DB (seeded here)."""
import os

import httpx
import pytest
from httpx import ASGITransport

from backend.app.main import app
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


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_health():
    async with _client() as c:
        r = await c.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


async def test_clients_then_info():
    async with _client() as c:
        clients = (await c.get("/api/clients")).json()
        assert clients
        guid = clients[0]["client_guid"]
        info = (await c.get(f"/api/clients/{guid}/info")).json()
    assert info["total_orders"] > 0


async def test_products_endpoints():
    async with _client() as c:
        n3s = (await c.get("/api/products/n3")).json()
        assert n3s
        n4s = (await c.get("/api/products/n4", params={"n3": n3s[0]})).json()
    assert n4s and "item_guid" in n4s[0]


async def test_analyze_contract():
    async with _client() as c:
        guid = (await c.get("/api/clients")).json()[0]["client_guid"]
        r = await c.post(
            "/api/analyze",
            json={"client_guid": guid, "order_lines": [{"n3": "x", "qty": 1}]},
        )
    assert r.status_code == 200
    body = r.json()
    assert {"analysis1_forgotten", "analysis2_niche", "niche"} <= set(body)


async def test_analyze_rejects_empty_order():
    async with _client() as c:
        guid = (await c.get("/api/clients")).json()[0]["client_guid"]
        r = await c.post("/api/analyze", json={"client_guid": guid, "order_lines": []})
    assert r.status_code == 400


async def test_sync_status():
    async with _client() as c:
        r = await c.get("/api/sync/status")
    assert r.status_code == 200 and "status" in r.json()
