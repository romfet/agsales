"""Service layer: orchestrates async data access + the pure analyzer."""
from __future__ import annotations

from backend.app import repository as repo
from backend.app.domain import analyzer


async def _resolve_n3(lines: list[dict]) -> list[dict]:
    """Normalize draft-order lines to {n3, qty}. Lines may arrive with item_guid
    (n3 resolved from the synced catalog) and/or n3 directly."""
    need = [l["item_guid"] for l in lines if not l.get("n3") and l.get("item_guid")]
    n3_by_item = await repo.get_n3_for_items(need) if need else {}
    out: list[dict] = []
    for l in lines:
        n3 = l.get("n3") or n3_by_item.get(l.get("item_guid"))
        if n3:
            out.append({"n3": n3, "qty": l.get("qty") or 0})
    return out


async def analyze(client_guid: str, lines: list[dict]) -> dict:
    """Run both analyses for a client + the current (draft) order.

    Stateless: nothing is written. ``lines`` is the draft order; the analyzer
    compares it (at N3 level) against the client's history and the niche.
    """
    order_lines = await _resolve_n3(lines)

    niche = await repo.get_client_niche(client_guid)

    client_profile = await repo.get_client_profile_n4(client_guid)
    forgotten = analyzer.analyze_forgotten(client_profile, order_lines)

    niche_recs: list[dict] = []
    if niche:
        niche_profile = await repo.get_niche_profile_n4(niche)
        bought = await repo.get_client_bought_item_guids(client_guid)
        niche_recs = analyzer.analyze_niche(niche, niche_profile, bought, order_lines)

    return {
        "client_guid": client_guid,
        "niche": niche,
        "analysis1_forgotten": forgotten,
        "analysis2_niche": niche_recs,
    }
