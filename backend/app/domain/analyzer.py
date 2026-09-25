"""Recommendation rules — pure functions over already-fetched profile rows.

Recommendations are calculated at N4 (SKU) grain. ``n3_id`` is retained as the
parent product group for display and downstream compatibility. Data access lives
in services.py / repository.py.
"""
from __future__ import annotations

# Analysis 1: min % of the client's own orders to flag a forgotten product.
MIN_FREQUENCY_PCT = 5
HIGH_FREQUENCY_PCT = 10        # frequency >= this -> priority "high"
# Analysis 2: min % of niche clients to recommend a product.
MIN_NICHE_PCT = 5
HIGH_NICHE_PCT = 8             # niche_pct >= this -> priority "high"

TOP_N = 5


def _order_index(order_lines: list[dict]) -> tuple[set[str], dict[str, float], set[str]]:
    """Build the exact N4 order index plus conservative N3-only exclusions.

    A legacy caller may send only ``n3_id``. In that case every N4 in that N3 is
    suppressed because the service cannot know which concrete SKU is present.
    """
    order_n4: set[str] = set()
    order_qty: dict[str, float] = {}
    n3_only: set[str] = set()
    for line in order_lines:
        n4 = line.get("n4_id")
        n3 = line.get("n3_id")
        if n4:
            order_n4.add(n4)
            order_qty[n4] = order_qty.get(n4, 0.0) + float(line.get("qty") or 0)
        elif n3:
            n3_only.add(n3)
    return order_n4, order_qty, n3_only


def analyze_forgotten(profile_n4: list[dict], order_lines: list[dict]) -> list[dict]:
    """Analysis 1: up to five historically frequent N4 absent from the draft."""
    order_n4, _, n3_only = _order_index(order_lines)
    results: list[dict] = []

    for row in profile_n4:
        n3 = row["n3_id"]
        n4 = row["n4_id"]
        freq_pct = row["frequency_pct"] or 0
        avg_qty = row["avg_qty_per_order"] or 0

        if n4 in order_n4 or n3 in n3_only or freq_pct < MIN_FREQUENCY_PCT:
            continue
        results.append({
            "type": "missing",
            "n3_id": n3, "n4_id": n4,
            "order_count": row["order_count"],
            "total_orders": row["total_orders"],
            "shipment_count": row["shipment_count"],
            "total_shipments": row["total_shipments"],
            "shipped_qty": round(row.get("total_qty_12m") or 0, 3),
            "frequency_pct": freq_pct,
            "avg_qty": round(avg_qty, 2),
            "current_qty": None,
            "priority": "high" if freq_pct >= HIGH_FREQUENCY_PCT else "medium",
        })

    results.sort(key=lambda x: (
        -x["frequency_pct"], -x["order_count"], -x["shipped_qty"], x["n4_id"]
    ))
    return results[:TOP_N]


def analyze_niche(
    niche: str,
    niche_profile_n4: list[dict],
    client_bought_n4_ids: set[str],
    order_lines: list[dict],
    excluded_n4_ids: set[str] | None = None,
) -> list[dict]:
    """Analysis 2: up to five popular niche N4 absent from the draft.

    ``excluded_n4_ids`` prevents duplicates with the client's own TOP-5.
    """
    order_n4, _, n3_only = _order_index(order_lines)
    excluded_n4_ids = excluded_n4_ids or set()
    results: list[dict] = []

    for row in niche_profile_n4:
        n3 = row["n3_id"]
        n4 = row["n4_id"]
        niche_pct = row["niche_pct"] or 0

        if (n4 not in order_n4 and n4 not in excluded_n4_ids
                and n3 not in n3_only and niche_pct >= MIN_NICHE_PCT):
            results.append({
                "n3_id": n3, "n4_id": n4,
                "niche": niche,
                "niche_pct": niche_pct,
                "client_count": row["client_count"],
                "total_clients": row["total_clients_in_niche"],
                "order_count": row["order_count"],
                "total_orders": row["total_orders_in_niche"],
                "shipment_count": row["shipment_count"],
                "total_shipments": row["total_shipments_in_niche"],
                "shipped_qty": round(row.get("total_qty_12m") or 0, 3),
                "frequency_pct": row["niche_freq_pct"] or 0,
                "client_bought_before": n4 in client_bought_n4_ids,
                "avg_qty_per_client": round(row["avg_qty_per_client"] or 0, 2),
                "priority": "high" if niche_pct >= HIGH_NICHE_PCT else "medium",
            })

    results.sort(key=lambda x: (
        -x["frequency_pct"], -x["order_count"], -x["shipped_qty"], x["n4_id"]
    ))
    return results[:TOP_N]
