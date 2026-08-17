"""Service-layer tests with mocked repository (no DB).

Guards the N3-grain contract: analyze() reads one row per subgroup and never
emits duplicate n3 recommendations (the old N4-grain bug).
"""
from backend.app import repository as repo, services


def _cp_n3(n3_id, n4_id, freq, avg=5.0, oc=8, to=10):
    return {"n3_id": n3_id, "n4_id": n4_id, "order_count": oc, "total_orders": to,
            "frequency_pct": freq, "avg_qty_per_order": avg}


def _np_n3(n3_id, n4_id, niche_pct, freq=20.0, cc=9, tc=10, oc=50, to=100, avg=4.0):
    return {"n3_id": n3_id, "n4_id": n4_id, "client_count": cc,
            "total_clients_in_niche": tc, "order_count": oc,
            "total_orders_in_niche": to, "niche_pct": niche_pct,
            "niche_freq_pct": freq, "avg_qty_per_client": avg}


async def test_analyze_is_n3_grain_and_deduped(monkeypatch):
    async def client_profile(_): return [_cp_n3("n3-A", "n4-A1", freq=80.0)]
    async def niche(_): return "Строительство"
    async def niche_profile(_): return [_np_n3("n3-Z", "n4-Z1", niche_pct=90.0)]
    async def bought(_): return {"n3-A"}
    async def n3_for_n4(_ids): return {}

    monkeypatch.setattr(repo, "get_client_profile_n3", client_profile)
    monkeypatch.setattr(repo, "get_client_niche", niche)
    monkeypatch.setattr(repo, "get_niche_profile_n3", niche_profile)
    monkeypatch.setattr(repo, "get_client_bought_n3_ids", bought)
    monkeypatch.setattr(repo, "get_n3_for_n4", n3_for_n4)

    res = await services.analyze("cli", lines=[])

    forgotten = res["analysis1_forgotten"]
    assert [r["n3_id"] for r in forgotten] == ["n3-A"]
    assert forgotten[0]["n4_id"] == "n4-A1"          # representative product carried
    assert forgotten[0]["type"] == "missing"

    niche_recs = res["analysis2_niche"]
    assert [r["n3_id"] for r in niche_recs] == ["n3-Z"]
    assert niche_recs[0]["client_bought_before"] is False

    # no subgroup appears twice in either list
    for lst in (forgotten, niche_recs):
        n3s = [r["n3_id"] for r in lst]
        assert len(n3s) == len(set(n3s))
