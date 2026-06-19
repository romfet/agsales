"""Recommendation rules — pure functions, ported from the original analyzer.py.

These operate on already-fetched profile rows (lists of dicts) so the business
logic is unit-testable without a database. Data access and stock enrichment live
in the service layer (services.py).

Thresholds and ranking are unchanged from the original implementation.
"""
from __future__ import annotations

# Analysis 1: min % of the client's own orders to flag a forgotten product.
MIN_FREQUENCY_PCT = 5
# Analysis 1: current qty < avg * this ratio -> "low quantity".
LOW_QTY_RATIO = 0.5
# Analysis 2: min % of niche clients to recommend a product.
MIN_NICHE_PCT = 5

TOP_N = 5


def _order_index(order_lines: list[dict]) -> tuple[set[str], dict[str, float]]:
    """Build (set of N3 in order, sum of qty per N3)."""
    order_n3: set[str] = set()
    order_qty: dict[str, float] = {}
    for line in order_lines:
        n3 = line.get("n3", "")
        if n3:
            order_n3.add(n3)
            order_qty[n3] = order_qty.get(n3, 0.0) + float(line.get("qty") or 0)
    return order_n3, order_qty


def analyze_forgotten(profile_n4: list[dict], order_lines: list[dict]) -> list[dict]:
    """Analysis 1: products this client usually buys but is missing/under-ordering now.

    ``profile_n4`` rows come from repository.get_client_profile_n4().
    Returns up to TOP_N recommendations ranked by frequency (no stock attached).
    """
    order_n3, order_qty = _order_index(order_lines)
    results: list[dict] = []

    for row in profile_n4:
        n3 = row["n3"]
        freq_pct = row["frequency_pct"] or 0
        avg_qty = row["avg_qty_per_order"] or 0

        if n3 not in order_n3:
            if freq_pct >= MIN_FREQUENCY_PCT:
                results.append({
                    "type": "missing",
                    "n3": n3,
                    "n4": row["n4"],
                    "item_guid": row["item_guid"],
                    "order_count": row["order_count"],
                    "total_orders": row["total_orders"],
                    "frequency_pct": freq_pct,
                    "avg_qty": round(avg_qty, 2),
                    "current_qty": None,
                    "priority": "high" if freq_pct >= 10 else "medium",
                })
        else:
            current_qty = order_qty.get(n3, 0)
            if avg_qty > 0 and current_qty < avg_qty * LOW_QTY_RATIO:
                results.append({
                    "type": "low_quantity",
                    "n3": n3,
                    "n4": row["n4"],
                    "item_guid": row["item_guid"],
                    "order_count": row["order_count"],
                    "total_orders": row["total_orders"],
                    "frequency_pct": freq_pct,
                    "avg_qty": round(avg_qty, 2),
                    "current_qty": round(current_qty, 2),
                    "priority": "medium",
                })

    results.sort(key=lambda x: x["frequency_pct"], reverse=True)
    return results[:TOP_N]


def analyze_niche(
    niche: str,
    niche_profile_n4: list[dict],
    client_bought_item_guids: set[str],
    order_lines: list[dict],
) -> list[dict]:
    """Analysis 2: products popular among same-niche clients, absent from this order.

    Returns up to TOP_N recommendations ranked by niche order-frequency.
    """
    order_n3, _ = _order_index(order_lines)
    results: list[dict] = []

    for row in niche_profile_n4:
        n3 = row["n3"]
        niche_pct = row["niche_pct"] or 0

        if n3 not in order_n3 and niche_pct >= MIN_NICHE_PCT:
            results.append({
                "n3": n3,
                "n4": row["n4"],
                "item_guid": row["item_guid"],
                "niche": niche,
                "niche_pct": niche_pct,
                "client_count": row["client_count"],
                "total_clients": row["total_clients_in_niche"],
                "order_count": row["order_count"],
                "total_orders": row["total_orders_in_niche"],
                "frequency_pct": row["niche_freq_pct"] or 0,
                "client_bought_before": row["item_guid"] in client_bought_item_guids,
                "avg_qty_per_client": round(row["avg_qty_per_client"] or 0, 2),
                "priority": "high" if niche_pct >= 8 else "medium",
            })

    results.sort(key=lambda x: x["frequency_pct"], reverse=True)
    return results[:TOP_N]
