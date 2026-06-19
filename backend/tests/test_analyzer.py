"""Pure unit tests for recommendation rules — no database required."""
from backend.app.domain import analyzer


def _cp(n3, n4, guid, freq, avg, oc=5, to=20):
    return {
        "n3": n3, "n4": n4, "item_guid": guid,
        "order_count": oc, "total_orders": to,
        "frequency_pct": freq, "avg_qty_per_order": avg,
    }


def test_forgotten_flags_missing_frequent_product():
    profile = [_cp("Арматура", "А500С 12", "g1", freq=40, avg=10)]
    recs = analyzer.analyze_forgotten(profile, order_lines=[])
    assert len(recs) == 1
    assert recs[0]["type"] == "missing"
    assert recs[0]["priority"] == "high"  # >= 10%


def test_forgotten_ignores_below_threshold():
    profile = [_cp("Арматура", "А500С 12", "g1", freq=3, avg=10)]  # < MIN_FREQUENCY_PCT
    assert analyzer.analyze_forgotten(profile, order_lines=[]) == []


def test_forgotten_flags_low_quantity_when_present():
    profile = [_cp("Лист", "Лист 8мм", "g2", freq=30, avg=10)]
    order = [{"n3": "Лист", "n4": "Лист 8мм", "qty": 2}]  # 2 < 10 * 0.5
    recs = analyzer.analyze_forgotten(profile, order)
    assert recs[0]["type"] == "low_quantity"
    assert recs[0]["current_qty"] == 2


def test_forgotten_ok_quantity_not_flagged():
    profile = [_cp("Лист", "Лист 8мм", "g2", freq=30, avg=10)]
    order = [{"n3": "Лист", "n4": "Лист 8мм", "qty": 8}]  # 8 >= 5
    assert analyzer.analyze_forgotten(profile, order) == []


def test_forgotten_sorted_and_capped_at_top_n():
    profile = [_cp("C%d" % i, "P%d" % i, "g%d" % i, freq=i, avg=5) for i in range(1, 12)]
    recs = analyzer.analyze_forgotten(profile, order_lines=[])
    assert len(recs) == analyzer.TOP_N
    freqs = [r["frequency_pct"] for r in recs]
    assert freqs == sorted(freqs, reverse=True)


def _np(n3, n4, guid, niche_pct, freq, cc=4, tc=10, oc=8, to=50, avg=3):
    return {
        "n3": n3, "n4": n4, "item_guid": guid,
        "client_count": cc, "total_clients_in_niche": tc,
        "order_count": oc, "total_orders_in_niche": to,
        "niche_pct": niche_pct, "niche_freq_pct": freq, "avg_qty_per_client": avg,
    }


def test_niche_recommends_absent_popular_product():
    prof = [_np("Труба", "Труба 40х20", "g9", niche_pct=12, freq=20)]
    recs = analyzer.analyze_niche("Строительство", prof, set(), order_lines=[])
    assert len(recs) == 1
    assert recs[0]["priority"] == "high"  # niche_pct >= 8
    assert recs[0]["client_bought_before"] is False


def test_niche_marks_client_bought_before():
    prof = [_np("Труба", "Труба 40х20", "g9", niche_pct=12, freq=20)]
    recs = analyzer.analyze_niche("Строительство", prof, {"g9"}, order_lines=[])
    assert recs[0]["client_bought_before"] is True


def test_niche_skips_products_in_current_order():
    prof = [_np("Труба", "Труба 40х20", "g9", niche_pct=12, freq=20)]
    order = [{"n3": "Труба", "n4": "x", "qty": 1}]
    assert analyzer.analyze_niche("Строительство", prof, set(), order) == []
