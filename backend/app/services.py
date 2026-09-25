"""Service layer: orchestrates async data access + the pure analyzer."""
from __future__ import annotations

from backend.app import repository as repo
from backend.app.domain import analyzer


async def _normalize_order_lines(lines: list[dict]) -> list[dict]:
    """Preserve N4-grain draft lines and resolve their parent N3 when needed.

    Legacy N3-only lines are retained. The analyzer treats them conservatively by
    suppressing every recommendation from that N3 rather than guessing the SKU.
    """
    need = [l["n4_id"] for l in lines if not l.get("n3_id") and l.get("n4_id")]
    n3_by_n4 = await repo.get_n3_for_n4(need) if need else {}
    out: list[dict] = []
    for l in lines:
        n4 = l.get("n4_id")
        n3 = l.get("n3_id") or n3_by_n4.get(n4)
        if n4 or n3:
            out.append({"n3_id": n3, "n4_id": n4, "qty": l.get("qty") or 0})
    return out


async def analyze(client_id: str, lines: list[dict], order_id: str | None = None) -> dict:
    """Run both analyses for a client + the current (draft) order. Выданные
    рекомендации логируются в recommendation_log (сигнал «ассистент сработал»)."""
    order_lines = await _normalize_order_lines(lines)

    summary = await repo.get_client_summary(client_id)   # шапка формы 1С
    niche = await repo.get_client_niche(client_id)

    # Профили на уровне конкретного товара N4; N3 сохраняется как родительская группа.
    client_profile = await repo.get_client_profile_n4(client_id)
    forgotten = analyzer.analyze_forgotten(client_profile, order_lines)

    niche_recs: list[dict] = []
    if niche:
        niche_profile = await repo.get_niche_profile_n4(niche)
        bought = {r["n4_id"] for r in client_profile}
        shown = {r["n4_id"] for r in forgotten}
        niche_recs = analyzer.analyze_niche(
            niche, niche_profile, bought, order_lines, excluded_n4_ids=shown
        )

    await repo.log_recommendations(client_id, order_id, forgotten, niche_recs)

    return {
        "client_id": client_id,
        "niche": niche,
        "client_summary": summary,
        "analysis1_forgotten": forgotten,
        "analysis2_niche": niche_recs,
    }
