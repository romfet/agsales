"""Тесты восстановления заказов из истории присылок (sync/rebuild_orders_from_versions).

Чистые тесты rebuild_order / sessions_from_gaps (без БД) + интеграционный прогон на
синтетической истории. Сценарии ревью 27.10.2026: длинный ночной сеанс не режется на
циклы, удалённая позиция не воскресает, повторный запуск ничего не меняет, запись
отказывается при поступлении данных во время расчёта."""
import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from backend.sync import rebuild_orders_from_versions as rb
from backend.sync.rebuild_orders_from_versions import rebuild, rebuild_order, sessions_from_gaps

T0 = datetime(2026, 10, 22, 19, 0, tzinfo=timezone.utc)
GAP = timedelta(hours=2)
NIGHT = 24 * 60


def v(seq, minutes, *lines):
    return {"seq": seq, "at": T0 + timedelta(minutes=minutes),
            "lines": [{"order_id": "O", "n3_id": "n3", "n4_id": n4, "qty": q} for n4, q in lines]}


def stream(*marks):
    """Глобальный поток версий: (seq, минуты). Сеансы — по паузам > 2 ч."""
    return sessions_from_gaps([(s, T0 + timedelta(minutes=m)) for s, m in marks], GAP)


def comp(version):
    return sorted((l["n4_id"], l["qty"]) for l in version["lines"])


def test_sessions_from_gaps():
    s = stream((1, 0), (2, 30), (3, 600), (4, NIGHT), (5, NIGHT + 60))
    assert [s[i] for i in (1, 2, 3, 4, 5)] == [0, 0, 1, 2, 2]


def test_long_night_session_is_one_cycle():
    """Ревью R1: товары одного заказа приходят в течение 12-часовой выгрузки —
    это один цикл (поток пачек без пауз), а не три."""
    hist = [v(1, 60, ("A", 1)), v(50, 300, ("M", 2)), v(99, 720, ("Z", 3))]
    sess = {1: 0, 50: 0, 99: 0}                          # между ними шли пачки других заказов
    new = rebuild_order(hist, sess)
    assert [comp(x) for x in new] == [[("A", 1), ("M", 2), ("Z", 3)]]


def test_two_nightly_sessions_by_product():
    """Как АГAG-133619: по товару в запросе, две ночные выгрузки."""
    hist = [v(1, 0, ("a", 1)), v(2, 1, ("b", 2)), v(3, 2, ("c", 3)),
            v(10, NIGHT, ("a", 1)), v(11, NIGHT + 1, ("b", 5)), v(12, NIGHT + 2, ("c", 3))]
    new = rebuild_order(hist, stream((1, 0), (2, 1), (3, 2), (10, NIGHT), (11, NIGHT + 1), (12, NIGHT + 2)))
    assert [comp(x) for x in new] == [[("a", 1), ("b", 2), ("c", 3)], [("a", 1), ("b", 5), ("c", 3)]]
    assert [x["seq"] for x in new] == [3, 12]
    assert new[0]["at"] == hist[0]["at"] and new[1]["at"] == hist[3]["at"]


def test_position_removed_between_nights_is_not_resurrected():
    """Ревью R2: ночь 1 — A, B; ночь 2 — только A (B удалили)."""
    hist = [v(1, 0, ("A", 1)), v(2, 1, ("B", 2)), v(10, NIGHT, ("A", 1))]
    new = rebuild_order(hist, stream((1, 0), (2, 1), (10, NIGHT)))
    assert comp(new[-1]) == [("A", 1)]


def test_full_document_history_is_kept_and_rerun_is_idempotent():
    """Ревью R2: история полных документов {A,B} → {A} не превращается в {A,B};
    повторный прогон по результату ничего не меняет."""
    hist = [v(1, 0, ("A", 1), ("B", 2)), v(10, NIGHT, ("A", 1))]
    sess = stream((1, 0), (10, NIGHT))
    new = rebuild_order(hist, sess)
    assert [comp(x) for x in new] == [[("A", 1), ("B", 2)], [("A", 1)]]
    again = rebuild_order([{"seq": x["seq"], "at": x["at"], "lines": x["lines"]} for x in new],
                          {x["seq"]: sess[x["seq"]] for x in new})
    assert [comp(x) for x in again] == [comp(x) for x in new]


def test_repeated_product_inside_session_starts_new_pass():
    """Две полные выгрузки подряд без паузы: повтор товара = новый проход."""
    hist = [v(1, 0, ("a", 1)), v(2, 1, ("b", 2)), v(3, 2, ("a", 4)), v(4, 3, ("b", 2))]
    new = rebuild_order(hist, stream((1, 0), (2, 1), (3, 2), (4, 3)))
    assert [comp(x) for x in new] == [[("a", 1), ("b", 2)], [("a", 4), ("b", 2)]]


def test_identical_sessions_give_one_version():
    hist = [v(1, 0, ("a", 1)), v(2, 1, ("b", 2)), v(3, NIGHT, ("a", 1)), v(4, NIGHT + 1, ("b", 2))]
    assert len(rebuild_order(hist, stream((1, 0), (2, 1), (3, NIGHT), (4, NIGHT + 1)))) == 1


def test_same_product_lines_multiplicity_within_version():
    hist = [v(1, 0, ("a", 3.824), ("a", 3.824)), v(2, 1, ("b", 1))]
    assert comp(rebuild_order(hist, stream((1, 0), (2, 1)))[-1]) == [("a", 3.824), ("a", 3.824), ("b", 1)]


# --- Интеграция ---------------------------------------------------------------

_DB = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")


async def _q(sql, **p):
    from backend.app.db.session import engine
    async with engine.begin() as conn:
        res = await conn.execute(text(sql), p)
        return res.all() if res.returns_rows else []


async def _seed_history():
    await _q("TRUNCATE ingest_batches")
    await _q("TRUNCATE order_line_versions")       # глобальные сеансы — только по этой истории
    await _q("DELETE FROM order_lines WHERE order_id LIKE 'RB-%'")
    for t in ("olv_backup_test", "_rebuild_versions", "_rebuild_orders"):
        await _q(f"DROP TABLE IF EXISTS {t}")

    async def ins(oid, seq, at, n4, qty):
        await _q("INSERT INTO order_line_versions (version_seq, ingested_at, content_hash, order_id, "
                 "order_num, order_date, client_id, niche, n3_id, n4_id, qty) VALUES "
                 "(:s, :at, :h, :o, :o, '2026-06-16', 'cli-rb', 'niche-rb', 'n3', :n4, :q)",
                 s=seq, at=at, h=f"h{seq}{oid}{n4}", o=oid, n4=n4, q=qty)

    # RB-1: 3 товара по одному, две ночи; в order_lines — только последний товар.
    for night in (0, 1):
        base = T0 + timedelta(days=night)
        for i, n4 in enumerate(("a", "b", "c")):
            await ins("RB-1", 9000 + night * 10 + i, base + timedelta(minutes=i * 120), n4, i + 1)
    await _q("INSERT INTO order_lines (order_id, order_num, order_date, client_id, niche, n3_id, n4_id, qty) "
             "VALUES ('RB-1', 'RB-1', '2026-06-16', 'cli-rb', 'niche-rb', 'n3', 'c', 3)")
    # RB-DEL: есть в версиях, нет в order_lines (удалён) — не воскрешать.
    await ins("RB-DEL", 9001, T0 + timedelta(minutes=1), "a", 1)


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_rebuild_on_synthetic_history():
    if not _DB:
        pytest.skip("no DATABASE_URL / TEST_DATABASE_URL set")
    await _seed_history()

    dry = await rebuild(gap_hours=5, dry_run=True)
    assert dry["sessions"] == 2                                   # шаги внутри ночи ≤ 4 ч < порога 5 ч
    assert dry["orders_changed"] == 1 and dry["products_added"] == 2
    assert dry["orders_only_in_versions"] == 1
    assert (await _q("SELECT count(*) FROM order_lines WHERE order_id = 'RB-1'"))[0][0] == 1

    with pytest.raises(ValueError):
        await rebuild(gap_hours=5, dry_run=False)                 # без backup-таблицы
    with pytest.raises(ValueError):
        await rebuild(gap_hours=5, dry_run=False, backup_table="olv_backup_test")  # без подтверждения
    with pytest.raises(ValueError):
        await rebuild(gap_hours=5, dry_run=False, backup_table="x; DROP TABLE y", confirm_backup=True)

    rep = await rebuild(gap_hours=5, dry_run=False, backup_table="olv_backup_test", confirm_backup=True)
    assert rep["orders_changed"] == 1
    lines = await _q("SELECT n4_id, qty FROM order_lines WHERE order_id = 'RB-1' ORDER BY n4_id")
    assert [(r.n4_id, float(r.qty)) for r in lines] == [("a", 1.0), ("b", 2.0), ("c", 3.0)]
    assert len(await _q("SELECT DISTINCT version_seq FROM order_line_versions WHERE order_id = 'RB-1'")) == 1
    assert not await _q("SELECT 1 FROM order_lines WHERE order_id = 'RB-DEL'")
    assert (await _q("SELECT count(*) FROM olv_backup_test WHERE order_id = 'RB-1'"))[0][0] == 6
    assert (await _q("SELECT to_regclass('_rebuild_versions')"))[0][0] is None   # scratch убран
    rows = await _q("SELECT is_new_position FROM effect_funnel WHERE order_id = 'RB-1'")
    assert rows and all(r.is_new_position == 0 for r in rows)    # витрина читает новую историю

    # Повторный запуск ничего не меняет.
    again = await rebuild(gap_hours=5, dry_run=True)
    assert again["orders_changed"] == 0
    await _q("DROP TABLE olv_backup_test")


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_rebuild_aborts_when_data_arrives_during_run(monkeypatch):
    """Ревью R3: приём direct во время расчёта → запись отказывается, данные целы."""
    if not _DB:
        pytest.skip("no DATABASE_URL / TEST_DATABASE_URL set")
    await _seed_history()
    real = rb._current_orders

    async def current_and_ingest(conn):
        res = await real(conn)
        await _q("INSERT INTO order_lines (order_id, order_num, client_id, n3_id, n4_id, qty) "
                 "VALUES ('RB-LIVE', 'RB-LIVE', 'cli-rb', 'n3', 'z', 1)")     # 1С пишет в это время
        return res

    monkeypatch.setattr(rb, "_current_orders", current_and_ingest)
    with pytest.raises(RuntimeError, match="новые данные"):
        await rebuild(gap_hours=5, dry_run=False, backup_table="olv_backup_test", confirm_backup=True)
    assert (await _q("SELECT count(*) FROM order_lines WHERE order_id = 'RB-1'"))[0][0] == 1  # не тронуто
    with pytest.raises(RuntimeError, match="_rebuild_versions"):            # прерванный запуск виден
        await rebuild(gap_hours=5, dry_run=False, backup_table="olv_backup_test2", confirm_backup=True)
    for t in ("olv_backup_test", "olv_backup_test2", "_rebuild_versions", "_rebuild_orders"):
        await _q(f"DROP TABLE IF EXISTS {t}")
    await _q("DELETE FROM order_lines WHERE order_id LIKE 'RB-%'")
