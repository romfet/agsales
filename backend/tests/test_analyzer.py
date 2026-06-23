"""Pure unit tests for recommendation rules — no database required."""
from backend.app.domain import analyzer


def _cp(n3_id, n4_id, freq, avg, oc=5, to=20):
    return {
        "n3_id": n3_id, "n4_id": n4_id,
        "order_count": oc, "total_orders": to,
        "frequency_pct": freq, "avg_qty_per_order": avg,
    }


def test_forgotten_flags_missing_frequent_product():
    recs = analyzer.analyze_forgotten([_cp("n3-arm", "n4-12", freq=40, avg=10)], order_lines=[])
    assert len(recs) == 1
    assert recs[0]["type"] == "missing" and recs[0]["priority"] == "high"


def test_forgotten_ignores_below_threshold():
    assert analyzer.analyze_forgotten([_cp("n3-arm", "n4-12", freq=3, avg=10)], order_lines=[]) == []


def test_forgotten_flags_low_quantity_when_present():
    profile = [_cp("n3-list", "n4-8", freq=30, avg=10)]
    order = [{"n3_id": "n3-list", "qty": 2}]  # 2 < 10 * 0.5
    recs = analyzer.analyze_forgotten(profile, order)
    assert recs[0]["type"] == "low_quantity" and recs[0]["current_qty"] == 2


def test_forgotten_ok_quantity_not_flagged():
    profile = [_cp("n3-list", "n4-8", freq=30, avg=10)]
    assert analyzer.analyze_forgotten(profile, [{"n3_id": "n3-list", "qty": 8}]) == []


def test_forgotten_sorted_and_capped_at_top_n():
    profile = [_cp("n3-%d" % i, "n4-%d" % i, freq=i, avg=5) for i in range(1, 12)]
    recs = analyzer.analyze_forgotten(profile, order_lines=[])
    assert len(recs) == analyzer.TOP_N
    freqs = [r["frequency_pct"] for r in recs]
    assert freqs == sorted(freqs, reverse=True)


def _np(n3_id, n4_id, niche_pct, freq, cc=4, tc=10, oc=8, to=50, avg=3):
    return {
        "n3_id": n3_id, "n4_id": n4_id,
        "client_count": cc, "total_clients_in_niche": tc,
        "order_count": oc, "total_orders_in_niche": to,
        "niche_pct": niche_pct, "niche_freq_pct": freq, "avg_qty_per_client": avg,
    }


def test_niche_recommends_absent_popular_product():
    recs = analyzer.analyze_niche("Строительство", [_np("n3-tr", "n4-9", niche_pct=12, freq=20)], set(), order_lines=[])
    assert len(recs) == 1
    assert recs[0]["priority"] == "high" and recs[0]["client_bought_before"] is False


def test_niche_marks_client_bought_before():
    recs = analyzer.analyze_niche("Строительство", [_np("n3-tr", "n4-9", niche_pct=12, freq=20)], {"n4-9"}, order_lines=[])
    assert recs[0]["client_bought_before"] is True


def test_niche_skips_products_in_current_order():
    prof = [_np("n3-tr", "n4-9", niche_pct=12, freq=20)]
    assert analyzer.analyze_niche("Строительство", prof, set(), [{"n3_id": "n3-tr", "qty": 1}]) == []
