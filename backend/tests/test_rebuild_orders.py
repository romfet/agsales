"""Тесты восстановления заказов из истории присылок (sync/rebuild_orders_from_versions).

Чистые тесты rebuild_order (без БД) + интеграционный прогон на синтетической
истории «заказ по одному товару, два ночных цикла»."""
import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from backend.sync.rebuild_orders_from_versions import rebuild, rebuild_order

T0 = datetime(2026, 10, 22, 19, 0, tzinfo=timezone.utc)
GAP = timedelta(hours=2)


def v(seq, minutes, *lines):
    return {"seq": seq, "at": T0 + timedelta(minutes=minutes),
            "lines": [{"order_id": "O", "n3_id": "n3", "n4_id": n4, "qty": q} for n4, q in lines]}


def comp(version):
    return sorted((l["n4_id"], l["qty"]) for l in version["lines"])


def test_two_nightly_cycles_by_product():
    """Как АГAG-133619: по товару в запросе, два цикла с паузой > 2 ч."""
    hist = [v(1, 0, ("a", 1)), v(2, 1, ("b", 2)), v(3, 2, ("c", 3)),
            v(10, 24 * 60, ("a", 1)), v(11, 24 * 60 + 1, ("b", 5)), v(12, 24 * 60 + 2, ("c", 3))]
    new = rebuild_order(hist, GAP)
    assert [comp(x) for x in new] == [[("a", 1), ("b", 2), ("c", 3)],
                                      [("a", 1), ("b", 5), ("c", 3)]]
    assert [x["seq"] for x in new] == [3, 12]
    assert new[0]["at"] == hist[0]["at"] and new[1]["at"] == hist[3]["at"]


def test_repeated_product_without_pause_starts_new_cycle():
    """Выгрузки подряд без паузы: повтор товара = новый проход."""
    hist = [v(1, 0, ("a", 1)), v(2, 1, ("b", 2)), v(3, 2, ("a", 4)), v(4, 3, ("b", 2))]
    new = rebuild_order(hist, GAP)
    assert [comp(x) for x in new] == [[("a", 1), ("b", 2)], [("a", 4), ("b", 2)]]


def test_identical_cycles_give_one_version():
    hist = [v(1, 0, ("a", 1)), v(2, 1, ("b", 2)), v(3, 300, ("a", 1)), v(4, 301, ("b", 2))]
    assert len(rebuild_order(hist, GAP)) == 1


def test_full_documents_from_old_direct_feed_are_kept():
    """Если 1С когда-то присылала заказ целиком — каждая версия остаётся версией."""
    hist = [v(1, 0, ("a", 1), ("b", 2)), v(2, 5, ("a", 1), ("b", 2), ("c", 7))]
    assert [comp(x) for x in rebuild_order(hist, GAP)] == [
        [("a", 1), ("b", 2)], [("a", 1), ("b", 2), ("c", 7)]]


def test_dedup_gap_product_is_carried_over():
    """Первый запрос цикла 2 совпал с последним запросом цикла 1 (товар c) —
    версия не записалась; c переносится из предыдущего цикла."""
    hist = [v(1, 0, ("a", 1)), v(2, 1, ("c", 3)), v(3, 300, ("a", 1))]
    new = rebuild_order(hist, GAP)
    assert comp(new[-1]) == [("a", 1), ("c", 3)]


def test_same_product_lines_multiplicity_within_version():
    hist = [v(1, 0, ("a", 3.824), ("a", 3.824)), v(2, 1, ("b", 1))]
    assert comp(rebuild_order(hist, GAP)[-1]) == [("a", 3.824), ("a", 3.824), ("b", 1)]


# --- Интеграция ---------------------------------------------------------------

_DB = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_rebuild_on_synthetic_history():
    if not _DB:
        pytest.skip("no DATABASE_URL / TEST_DATABASE_URL set")
    from backend.app.db.session import engine

    async def q(sql, **p):
        async with engine.begin() as conn:
            res = await conn.execute(text(sql), p)
            return res.all() if res.returns_rows else []

    await q("TRUNCATE ingest_batches")
    await q("DELETE FROM order_line_versions WHERE order_id LIKE 'RB-%'")
    await q("DELETE FROM order_lines WHERE order_id LIKE 'RB-%'")
    await q("DROP TABLE IF EXISTS olv_backup_test")

    def ins_version(oid, seq, at, n4, qty):
        return q("INSERT INTO order_line_versions (version_seq, ingested_at, content_hash, order_id, "
                 "order_num, order_date, client_id, niche, n3_id, n4_id, qty) VALUES "
                 "(:s, :at, :h, :o, :o, '2026-06-16', 'cli-rb', 'niche-rb', 'n3', :n4, :q)",
                 s=seq, at=at, h=f"h{seq}{n4}", o=oid, n4=n4, q=qty)

    # RB-1: 3 товара по одному, два цикла; в order_lines — только последний товар.
    for cyc, base in ((0, T0), (1, T0 + timedelta(days=1))):
        for i, n4 in enumerate(("a", "b", "c")):
            await ins_version("RB-1", 9000 + cyc * 10 + i, base + timedelta(minutes=i), n4, i + 1)
    await q("INSERT INTO order_lines (order_id, order_num, order_date, client_id, niche, n3_id, n4_id, qty) "
            "VALUES ('RB-1', 'RB-1', '2026-06-16', 'cli-rb', 'niche-rb', 'n3', 'c', 3)")
    # RB-DEL: есть в версиях, нет в order_lines (удалён) — не воскрешать.
    await ins_version("RB-DEL", 9100, T0, "a", 1)

    dry = await rebuild(gap_hours=2, dry_run=True)
    assert dry["orders_changed"] >= 1 and dry["products_added"] >= 2
    assert dry["orders_only_in_versions"] >= 1
    (n,), = await q("SELECT count(*) FROM order_lines WHERE order_id = 'RB-1'")
    assert n == 1                                                  # dry-run ничего не пишет

    with pytest.raises(ValueError):
        await rebuild(gap_hours=2, dry_run=False)                  # без backup-таблицы нельзя

    rep = await rebuild(gap_hours=2, dry_run=False, backup_table="olv_backup_test")
    assert rep["orders_changed"] >= 1
    lines = await q("SELECT n4_id, qty FROM order_lines WHERE order_id = 'RB-1' ORDER BY n4_id")
    assert [(r.n4_id, float(r.qty)) for r in lines] == [("a", 1.0), ("b", 2.0), ("c", 3.0)]
    vers = await q("SELECT DISTINCT version_seq FROM order_line_versions WHERE order_id = 'RB-1'")
    assert len(vers) == 1                                          # два одинаковых цикла → одна версия
    assert not await q("SELECT 1 FROM order_lines WHERE order_id = 'RB-DEL'")
    (bk,), = await q("SELECT count(*) FROM olv_backup_test WHERE order_id = 'RB-1'")
    assert bk == 6                                                 # старая история сохранена

    # Витрина эффекта читает новую историю (а не копию): все товары — исходные.
    rows = await q("SELECT is_new_position FROM effect_funnel WHERE order_id = 'RB-1'")
    assert rows and all(r.is_new_position == 0 for r in rows)

    with pytest.raises(RuntimeError):
        await rebuild(gap_hours=2, dry_run=False, backup_table="olv_backup_test")  # уже есть
    await q("DROP TABLE olv_backup_test")
