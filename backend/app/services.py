"""Service layer: orchestrates async data access + the pure analyzer."""
from __future__ import annotations

from backend.app import repository as repo
from backend.app.domain import analyzer


async def _resolve_n3(lines: list[dict]) -> list[dict]:
    """Normalize draft-order lines to {n3_id, qty}. Lines may carry n4_id
    (n3 resolved from the synced catalog) and/or n3_id directly."""
    need = [l["n4_id"] for l in lines if not l.get("n3_id") and l.get("n4_id")]
    n3_by_n4 = await repo.get_n3_for_n4(need) if need else {}
    out: list[dict] = []
    for l in lines:
        n3 = l.get("n3_id") or n3_by_n4.get(l.get("n4_id"))
        if n3:
            out.append({"n3_id": n3, "qty": l.get("qty") or 0})
    return out


async def analyze(client_id: str, lines: list[dict]) -> dict:
    """Run both analyses for a client + the current (draft) order. Stateless."""
    order_lines = await _resolve_n3(lines)

    niche = await repo.get_client_niche(client_id)

    client_profile = await repo.get_client_profile_n4(client_id)
    forgotten = analyzer.analyze_forgotten(client_profile, order_lines)

    niche_recs: list[dict] = []
    if niche:
        niche_profile = await repo.get_niche_profile_n4(niche)
        bought = await repo.get_client_bought_n4_ids(client_id)
        niche_recs = analyzer.analyze_niche(niche, niche_profile, bought, order_lines)

    return {
        "client_id": client_id,
        "niche": niche,
        "analysis1_forgotten": forgotten,
        "analysis2_niche": niche_recs,
    }
