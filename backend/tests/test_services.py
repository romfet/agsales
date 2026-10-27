"""Service-layer tests with mocked repository (no DB)."""
from datetime import date

import pytest
from pydantic import ValidationError

from backend.app import repository as repo, services
from backend.app.schemas import AnalyzeIn


def _cp_n3(n3_id, n4_id, freq, avg=5.0, oc=8, to=10, sc=12, ts=15):
    return {"n3_id": n3_id, "n4_id": n4_id, "order_count": oc, "total_orders": to,
            "shipment_count": sc, "total_shipments": ts,
            "frequency_pct": freq, "avg_qty_per_order": avg}


def _np_n3(n3_id, n4_id, niche_pct, freq=20.0, cc=9, tc=10, oc=50, to=100, sc=80, tsh=140, avg=4.0):
    return {"n3_id": n3_id, "n4_id": n4_id, "client_count": cc,
            "total_clients_in_niche": tc, "order_count": oc,
            "total_orders_in_niche": to, "shipment_count": sc,
            "total_shipments_in_niche": tsh, "niche_pct": niche_pct,
            "niche_freq_pct": freq, "avg_qty_per_client": avg}


async def test_analyze_is_n4_grain_and_deduped_between_lists(monkeypatch):
    async def client_profile(_, _from=None, _to=None): return [_cp_n3("n3-A", "n4-A1", freq=80.0)]
    async def niche(_): return "Строительство"
    async def niche_profile(_, _from=None, _to=None): return [
        _np_n3("n3-A", "n4-A1", niche_pct=95.0),
        _np_n3("n3-Z", "n4-Z1", niche_pct=90.0),
    ]
    async def n3_for_n4(_ids): return {}
    async def log_rec(*a, **k): return None
    async def summary(_, _from=None, _to=None):
        return {"shipped_qty": 200.0, "last_shipment_date": "2026-05-13",
                "orders_in_history": 4, "shipments_in_history": 11}

    monkeypatch.setattr(repo, "get_client_profile_n4", client_profile)
    monkeypatch.setattr(repo, "get_client_niche", niche)
    monkeypatch.setattr(repo, "get_niche_profile_n4", niche_profile)
    monkeypatch.setattr(repo, "get_n3_for_n4", n3_for_n4)
    monkeypatch.setattr(repo, "log_recommendations", log_rec)
    monkeypatch.setattr(repo, "get_client_summary", summary)

    res = await services.analyze("cli", lines=[])

    assert res["client_summary"]["shipped_qty"] == 200.0        # Отгрузка за период
    assert res["client_summary"]["orders_in_history"] == 4       # по заказам
    assert res["client_summary"]["shipments_in_history"] == 11   # по отгрузкам (РТУ)

    forgotten = res["analysis1_forgotten"]
    assert [r["n3_id"] for r in forgotten] == ["n3-A"]
    assert forgotten[0]["n4_id"] == "n4-A1"
    assert forgotten[0]["type"] == "missing"

    niche_recs = res["analysis2_niche"]
    assert [r["n3_id"] for r in niche_recs] == ["n3-Z"]
    assert niche_recs[0]["client_bought_before"] is False

    # The same N4 is never emitted in both lists.
    assert {r["n4_id"] for r in forgotten}.isdisjoint(
        {r["n4_id"] for r in niche_recs}
    )


async def test_normalize_preserves_n4_and_resolves_parent_n3(monkeypatch):
    async def n3_for_n4(_ids): return {"n4-A": "n3-A"}
    monkeypatch.setattr(repo, "get_n3_for_n4", n3_for_n4)

    assert await services._normalize_order_lines([{"n4_id": "n4-A", "qty": 2}]) == [
        {"n3_id": "n3-A", "n4_id": "n4-A", "qty": 2}
    ]


async def test_analyze_passes_period_to_all_calculations(monkeypatch):
    seen = []

    async def summary(client_id, period_from, period_to):
        seen.append(("summary", client_id, period_from, period_to))
        return {"shipped_qty": 0.0, "last_shipment_date": None,
                "orders_in_history": 0, "shipments_in_history": 0}

    async def client_profile(client_id, period_from, period_to):
        seen.append(("client", client_id, period_from, period_to))
        return []

    async def niche(client_id):
        return "niche-X"

    async def niche_profile(niche_id, period_from, period_to):
        seen.append(("niche", niche_id, period_from, period_to))
        return []

    async def n3_for_n4(_ids): return {}
    async def log_rec(*_args, **_kwargs): return None

    monkeypatch.setattr(repo, "get_client_summary", summary)
    monkeypatch.setattr(repo, "get_client_profile_n4", client_profile)
    monkeypatch.setattr(repo, "get_client_niche", niche)
    monkeypatch.setattr(repo, "get_niche_profile_n4", niche_profile)
    monkeypatch.setattr(repo, "get_n3_for_n4", n3_for_n4)
    monkeypatch.setattr(repo, "log_recommendations", log_rec)

    start, end = date(2026, 3, 1), date(2026, 5, 31)
    await services.analyze("cli", [], period_from=start, period_to=end)

    assert seen == [
        ("summary", "cli", start, end),
        ("client", "cli", start, end),
        ("niche", "niche-X", start, end),
    ]


def test_analyze_period_validation():
    valid = AnalyzeIn.model_validate({
        "client_id": "cli",
        "period_from": "2026-03-01",
        "period_to": "2026-05-31",
        "lines": [{"n4_id": "n4", "qty": 1}],
    })
    assert valid.period_from == date(2026, 3, 1)
    assert valid.period_to == date(2026, 5, 31)

    # Одна граница — открытый интервал (репозиторий фильтрует по заданной границе).
    one = AnalyzeIn.model_validate({"client_id": "cli", "period_from": "2026-03-01", "lines": []})
    assert one.period_from == date(2026, 3, 1) and one.period_to is None

    # Даты в формах 1С: пустая дата и "" → граница не задана; время отбрасывается.
    def period(pf, pt):
        m = AnalyzeIn.model_validate({"client_id": "cli", "period_from": pf, "period_to": pt, "lines": []})
        return m.period_from, m.period_to

    assert period("0001-01-01T00:00:00", "0001-01-01") == (None, None)
    assert period("", "  ") == (None, None)
    assert period("2026-03-01T00:00:00", "2026-05-31T23:59:59") == (date(2026, 3, 1), date(2026, 5, 31))
    assert period("01.03.2026", "31.05.2026") == (date(2026, 3, 1), date(2026, 5, 31))

    # Порядок границ проверяет эндпоинт (422 {"detail": "..."}), модель его принимает.
    rev = AnalyzeIn.model_validate({
        "client_id": "cli", "period_from": "2026-06-01", "period_to": "2026-05-31", "lines": []})
    assert rev.period_from > rev.period_to

    with pytest.raises(ValidationError, match="не удалось разобрать дату"):
        AnalyzeIn.model_validate({"client_id": "cli", "period_from": "вчера", "lines": []})
