"""Service layer: orchestrates async data access + pure analyzer + stock enrichment."""
from __future__ import annotations

from backend.app import repository as repo
from backend.app.domain import analyzer


async def _attach_stock(recs: list[dict]) -> list[dict]:
    """Annotate each recommendation with on_stock / in_transit (by item_guid)."""
    guids = [r["item_guid"] for r in recs if r.get("item_guid")]
    stock = await repo.get_stock_map(guids)
    for r in recs:
        info = stock.get(r.get("item_guid"))
        r["stock_on"] = info["on_stock"] if info else None
        r["stock_in_transit"] = info["in_transit"] if info else None
    return recs


async def analyze(client_guid: str, order_lines: list[dict]) -> dict:
    """Run both analyses for a client + in-progress order."""
    niche = await repo.get_client_niche(client_guid)

    client_profile = await repo.get_client_profile_n4(client_guid)
    forgotten = analyzer.analyze_forgotten(client_profile, order_lines)

    niche_recs: list[dict] = []
    if niche:
        niche_profile = await repo.get_niche_profile_n4(niche)
        bought = await repo.get_client_bought_item_guids(client_guid)
        niche_recs = analyzer.analyze_niche(niche, niche_profile, bought, order_lines)

    await _attach_stock(forgotten)
    await _attach_stock(niche_recs)

    return {
        "client_guid": client_guid,
        "niche": niche,
        "analysis1_forgotten": forgotten,
        "analysis2_niche": niche_recs,
    }
