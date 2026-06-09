"""
Recommendation engine — two analysis algorithms.
"""
import data_store

# Thresholds (set for N4 product-level granularity)
MIN_FREQUENCY_PCT = 5    # Analysis 1: min % of orders to flag as "forgotten"
LOW_QTY_RATIO = 0.5      # Analysis 1: current qty < avg * this ratio → "low quantity"
MIN_NICHE_PCT = 5         # Analysis 2: min % of niche clients to recommend


def _attach_stock(item: dict) -> dict:
    """Attach on_stock / in_transit (tonnes) for the item's N4. None if absent."""
    info = data_store.get_stock_for_n4(item.get("n4"))
    if info is None:
        item["stock_on"] = None
        item["stock_in_transit"] = None
    else:
        on = info["on_stock"]
        intr = info["in_transit"]
        item["stock_on"] = round(on, 3) if on is not None else None
        item["stock_in_transit"] = round(intr, 3) if intr is not None else None
    return item


TOP_N = 5


def analyze_forgotten(client: str, order_lines: list[dict]) -> list[dict]:
    """
    Analysis 1: What did this customer forget?
    Compares current order against client's own purchase history at N4 (product) level.

    order_lines: [{"n3": "...", "qty": float}, ...]
    Returns list of recommendation dicts per individual product (N4).
    """
    profile_n4 = data_store.get_client_profile_n4(client)
    if profile_n4.empty:
        return []

    # Build set of N3 in current order and qty map
    order_n3 = set()
    order_qty = {}
    for line in order_lines:
        n3 = line.get("n3", "")
        if n3:
            order_n3.add(n3)
            order_qty[n3] = order_qty.get(n3, 0) + float(line.get("qty", 0))

    total_orders = data_store.get_client_total_orders(client)
    results = []

    for _, row in profile_n4.iterrows():
        n3 = row[data_store.COL_N3]
        n4 = row[data_store.COL_N4]
        freq_pct = row["frequency_pct"]
        avg_qty = row["avg_qty_per_order"]

        if n3 not in order_n3:
            # Missing product category — show each N4 product
            if freq_pct >= MIN_FREQUENCY_PCT:
                priority = "high" if freq_pct >= 10 else "medium"
                results.append({
                    "type": "missing",
                    "n3": n3,
                    "n4": n4,
                    "order_count": int(row["order_count"]),
                    "total_orders": int(total_orders),
                    "frequency_pct": freq_pct,
                    "avg_qty": round(avg_qty, 2),
                    "current_qty": None,
                    "priority": priority,
                })
        else:
            # Present but check quantity at N3 level
            current_qty = order_qty.get(n3, 0)
            if avg_qty > 0 and current_qty < avg_qty * LOW_QTY_RATIO:
                results.append({
                    "type": "low_quantity",
                    "n3": n3,
                    "n4": n4,
                    "order_count": int(row["order_count"]),
                    "total_orders": int(total_orders),
                    "frequency_pct": freq_pct,
                    "avg_qty": round(avg_qty, 2),
                    "current_qty": round(current_qty, 2),
                    "priority": "medium",
                })

    # Rank by order frequency descending and keep the top-N most frequent
    results.sort(key=lambda x: x["frequency_pct"], reverse=True)
    return [_attach_stock(r) for r in results[:TOP_N]]


def analyze_niche(client: str, order_lines: list[dict]) -> list[dict]:
    """
    Analysis 2: What do similar customers in the same niche buy?
    Compares current order against niche-level product popularity at N4 (product) level.

    Returns list of recommendation dicts per individual product (N4).
    """
    niche = data_store.get_client_niche(client)
    if not niche:
        return []

    niche_prof_n4 = data_store.get_niche_profile_n4(niche)
    if niche_prof_n4.empty:
        return []

    # Client's own history — set of N4 products they ever bought
    client_prof_n4 = data_store.get_client_profile_n4(client)
    client_ever_bought_n4 = set()
    if not client_prof_n4.empty:
        client_ever_bought_n4 = set(client_prof_n4[data_store.COL_N4].tolist())

    # Current order N3
    order_n3 = {line.get("n3", "") for line in order_lines if line.get("n3")}

    results = []

    for _, row in niche_prof_n4.iterrows():
        n3 = row[data_store.COL_N3]
        n4 = row[data_store.COL_N4]
        niche_pct = row["niche_pct"]

        if n3 not in order_n3 and niche_pct >= MIN_NICHE_PCT:
            priority = "high" if niche_pct >= 8 else "medium"
            results.append({
                "n3": n3,
                "n4": n4,
                "niche": niche,
                "niche_pct": niche_pct,
                "client_count": int(row["client_count"]),
                "total_clients": int(row["total_clients_in_niche"]),
                # Order-based frequency across the niche (how often it is ordered)
                "order_count": int(row["order_count"]),
                "total_orders": int(row["total_orders_in_niche"]),
                "frequency_pct": row["niche_freq_pct"],
                "client_bought_before": n4 in client_ever_bought_n4,
                "avg_qty_per_client": round(row["avg_qty_per_client"], 2),
                "priority": priority,
            })

    # Rank by order frequency across the niche descending; keep the top-N most frequent
    results.sort(key=lambda x: x["frequency_pct"], reverse=True)
    return [_attach_stock(r) for r in results[:TOP_N]]
