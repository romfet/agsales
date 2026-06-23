"""Recommendation rules — pure functions over already-fetched profile rows.

Matching is at N3 level by **n3_id** (GUID); n4_id is carried through (1С resolves
names by GUID). Data access lives in services.py / repository.py.
"""
from __future__ import annotations

# Analysis 1: min % of the client's own orders to flag a forgotten product.
MIN_FREQUENCY_PCT = 5
HIGH_FREQUENCY_PCT = 10        # frequency >= this -> priority "high"
# Analysis 1: current qty < avg * this ratio -> "low quantity".
LOW_QTY_RATIO = 0.5
# Analysis 2: min % of niche clients to recommend a product.
MIN_NICHE_PCT = 5
HIGH_NICHE_PCT = 8             # niche_pct >= this -> priority "high"

TOP_N = 5


def _order_index(order_lines: list[dict]) -> tuple[set[str], dict[str, float]]:
    """Build (set of n3_id in order, sum of qty per n3_id)."""
    order_n3: set[str] = set()
    order_qty: dict[str, float] = {}
    for line in order_lines:
        n3 = line.get("n3_id", "")
        if n3:
            order_n3.add(n3)
            order_qty[n3] = order_qty.get(n3, 0.0) + float(line.get("qty") or 0)
    return order_n3, order_qty


def analyze_forgotten(profile_n4: list[dict], order_lines: list[dict]) -> list[dict]:
    """Analysis 1: products this client usually buys but is missing/under-ordering now."""
    order_n3, order_qty = _order_index(order_lines)
    results: list[dict] = []

    for row in profile_n4:
        n3 = row["n3_id"]
        freq_pct = row["frequency_pct"] or 0
        avg_qty = row["avg_qty_per_order"] or 0

        if n3 not in order_n3:
            if freq_pct >= MIN_FREQUENCY_PCT:
                results.append({
                    "type": "missing",
                    "n3_id": n3, "n4_id": row["n4_id"],
                    "order_count": row["order_count"],
                    "total_orders": row["total_orders"],
                    "frequency_pct": freq_pct,
                    "avg_qty": round(avg_qty, 2),
                    "current_qty": None,
                    "priority": "high" if freq_pct >= HIGH_FREQUENCY_PCT else "medium",
                })
        else:
            current_qty = order_qty.get(n3, 0)
            if avg_qty > 0 and current_qty < avg_qty * LOW_QTY_RATIO:
                results.append({
                    "type": "low_quantity",
                    "n3_id": n3, "n4_id": row["n4_id"],
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
    client_bought_n4_ids: set[str],
    order_lines: list[dict],
) -> list[dict]:
    """Analysis 2: products popular among same-niche clients, absent from this order."""
    order_n3, _ = _order_index(order_lines)
    results: list[dict] = []

    for row in niche_profile_n4:
        n3 = row["n3_id"]
        niche_pct = row["niche_pct"] or 0

        if n3 not in order_n3 and niche_pct >= MIN_NICHE_PCT:
            results.append({
                "n3_id": n3, "n4_id": row["n4_id"],
                "niche": niche,
                "niche_pct": niche_pct,
                "client_count": row["client_count"],
                "total_clients": row["total_clients_in_niche"],
                "order_count": row["order_count"],
                "total_orders": row["total_orders_in_niche"],
                "frequency_pct": row["niche_freq_pct"] or 0,
                "client_bought_before": row["n4_id"] in client_bought_n4_ids,
                "avg_qty_per_client": round(row["avg_qty_per_client"] or 0, 2),
                "priority": "high" if niche_pct >= HIGH_NICHE_PCT else "medium",
            })

    results.sort(key=lambda x: x["frequency_pct"], reverse=True)
    return results[:TOP_N]
