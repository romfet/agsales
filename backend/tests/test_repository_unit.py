"""Чистые юнит-тесты хелперов repository (без БД).

Импорт repository строит engine из settings, но не подключается — тесты не требуют
живой БД. Стерегут устойчивость парсера даты (ingest не должен падать на не-ISO дате)
и семантику content_hash (дедуп версий заказа)."""
from datetime import date, datetime

from backend.app.repository import _content_hash, _to_date


def test_to_date_iso_and_datetime():
    assert _to_date("2025-01-01") == date(2025, 1, 1)
    assert _to_date("2025-01-01T08:30:00") == date(2025, 1, 1)   # ISO с временем
    assert _to_date(datetime(2025, 1, 1, 8, 30)) == date(2025, 1, 1)
    assert _to_date(date(2025, 1, 1)) == date(2025, 1, 1)


def test_to_date_non_iso_formats():
    assert _to_date("01.02.2025") == date(2025, 2, 1)   # dd.mm.yyyy
    assert _to_date("2025/02/01") == date(2025, 2, 1)   # yyyy/mm/dd


def test_to_date_bad_returns_none_not_raises():
    # Непарсируемое → None, НЕ исключение (иначе падает вся пачка ingest).
    assert _to_date("не дата") is None
    assert _to_date("") is None
    assert _to_date(None) is None


def test_content_hash_order_independent_and_qty_sensitive():
    a = _content_hash([{"n3_id": "n3", "n4_id": "n4", "qty": 1.0},
                       {"n3_id": "m3", "n4_id": "m4", "qty": 2.0}])
    b = _content_hash([{"n3_id": "m3", "n4_id": "m4", "qty": 2.0},
                       {"n3_id": "n3", "n4_id": "n4", "qty": 1.0}])
    assert a == b   # порядок строк не влияет
    c = _content_hash([{"n3_id": "n3", "n4_id": "n4", "qty": 1.5},
                       {"n3_id": "m3", "n4_id": "m4", "qty": 2.0}])
    assert a != c   # изменение qty меняет хэш
