"""Pure unit tests for recommendation rules — no database required."""
from backend.app.domain import analyzer


def _cp(n3_id, n4_id, freq, avg, oc=5, to=20, sc=9, ts=30, tq=123.456):
    return {
        "n3_id": n3_id, "n4_id": n4_id,
        "order_count": oc, "total_orders": to,           # по ЗАКАЗАМ
        "shipment_count": sc, "total_shipments": ts,     # по ОТГРУЗКАМ (РТУ)
        "frequency_pct": freq, "avg_qty_per_order": avg,
        "total_qty_12m": tq,
    }


def test_forgotten_flags_missing_frequent_product():
    recs = analyzer.analyze_forgotten(
        [_cp("n3-arm", "n4-12", freq=40, avg=10, oc=5, to=20, sc=9, ts=30, tq=42.5)], order_lines=[])
    assert len(recs) == 1
    assert recs[0]["type"] == "missing" and recs[0]["priority"] == "high"
    assert recs[0]["shipped_qty"] == 42.5        # Σ отгружено тонн по РТУ
    # Счётчики заказов и отгрузок прокидываются раздельно (не путаются местами).
    assert recs[0]["order_count"] == 5 and recs[0]["total_orders"] == 20
    assert recs[0]["shipment_count"] == 9 and recs[0]["total_shipments"] == 30


def test_forgotten_ignores_below_threshold():
    assert analyzer.analyze_forgotten([_cp("n3-arm", "n4-12", freq=3, avg=10)], order_lines=[]) == []


def test_forgotten_skips_exact_n4_already_in_order():
    profile = [_cp("n3-list", "n4-8", freq=30, avg=10)]
    order = [{"n3_id": "n3-list", "n4_id": "n4-8", "qty": 2}]
    assert analyzer.analyze_forgotten(profile, order) == []


def test_forgotten_can_recommend_another_n4_from_same_n3():
    profile = [_cp("n3-list", "n4-8", freq=30, avg=10)]
    order = [{"n3_id": "n3-list", "n4_id": "n4-10", "qty": 8}]
    assert [r["n4_id"] for r in analyzer.analyze_forgotten(profile, order)] == ["n4-8"]


def test_forgotten_n3_only_line_suppresses_entire_group():
    profile = [_cp("n3-list", "n4-8", freq=30, avg=10)]
    assert analyzer.analyze_forgotten(profile, [{"n3_id": "n3-list", "qty": 8}]) == []


def test_forgotten_sorted_and_capped_at_top_n():
    profile = [_cp("n3-%d" % i, "n4-%d" % i, freq=i, avg=5) for i in range(1, 12)]
    recs = analyzer.analyze_forgotten(profile, order_lines=[])
    assert len(recs) == analyzer.TOP_N
    freqs = [r["frequency_pct"] for r in recs]
    assert freqs == sorted(freqs, reverse=True)


def _np(n3_id, n4_id, niche_pct, freq, cc=4, tc=10, oc=8, to=50, sc=15, tsh=90, avg=3, tq=987.654):
    return {
        "n3_id": n3_id, "n4_id": n4_id,
        "client_count": cc, "total_clients_in_niche": tc,
        "order_count": oc, "total_orders_in_niche": to,              # по ЗАКАЗАМ
        "shipment_count": sc, "total_shipments_in_niche": tsh,       # по ОТГРУЗКАМ (РТУ)
        "niche_pct": niche_pct, "niche_freq_pct": freq, "avg_qty_per_client": avg,
        "total_qty_12m": tq,
    }


def test_niche_recommends_absent_popular_product():
    recs = analyzer.analyze_niche(
        "Строительство",
        [_np("n3-tr", "n4-9", niche_pct=12, freq=20, oc=8, to=50, sc=15, tsh=90, tq=77.0)],
        set(), order_lines=[])
    assert len(recs) == 1
    assert recs[0]["priority"] == "high" and recs[0]["client_bought_before"] is False
    assert recs[0]["shipped_qty"] == 77.0        # Σ отгружено тонн в нише по РТУ
    # order_* — по заказам, shipment_* — по отгрузкам; знаменатели ниши не перепутаны.
    assert recs[0]["order_count"] == 8 and recs[0]["total_orders"] == 50
    assert recs[0]["shipment_count"] == 15 and recs[0]["total_shipments"] == 90


def test_niche_marks_client_bought_before_by_n4():
    recs = analyzer.analyze_niche("Строительство", [_np("n3-tr", "n4-9", niche_pct=12, freq=20)], {"n3-tr"}, order_lines=[])
    assert recs[0]["client_bought_before"] is False

    recs = analyzer.analyze_niche("Строительство", [_np("n3-tr", "n4-9", niche_pct=12, freq=20)], {"n4-9"}, order_lines=[])
    assert recs[0]["client_bought_before"] is True


def test_niche_excludes_duplicates_already_shown_in_client_top():
    prof = [_np("n3-tr", "n4-9", niche_pct=12, freq=20)]
    assert analyzer.analyze_niche(
        "Строительство", prof, set(), [], excluded_n4_ids={"n4-9"}
    ) == []


def test_niche_skips_products_in_current_order():
    prof = [_np("n3-tr", "n4-9", niche_pct=12, freq=20)]
    assert analyzer.analyze_niche(
        "Строительство", prof, set(), [{"n3_id": "n3-tr", "n4_id": "n4-9", "qty": 1}]
    ) == []
