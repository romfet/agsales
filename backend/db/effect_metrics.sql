-- ============================================================================
-- Эффект ИИ-ассистента по МАРКЕРУ рекомендации (робастный путь).
-- ============================================================================
-- Строки заказа с rec_source=true — это позиции, ДОБАВЛЕННЫЕ из принятой
-- рекомендации ИИ (маркер проставляет 1С). Эффект = прямое чтение таких строк,
-- без матчинга/реплея/угадывания. Сценарий определяется по origin:
--   rec_origin_order = order_num (или NULL)  → Сценарий №1 (в тот же заказ);
--   rec_origin_order ≠ order_num             → Сценарий №2 (новый заказ).
--
-- Это обычный VIEW поверх order_lines (текущий срез = реализованные заказы) —
-- матвью не нужны. Подключать в прод-схему можно, когда 1С начнёт слать маркер.
--
-- ЧЕГО ЗДЕСЬ НЕТ (осознанно): доля/конверсия (колонки 6,10 таблицы §5 спеки)
-- требует знаменателя «сколько заказов ассистент отработал» — это отдельный
-- сигнал (кол-во показов / настройки исключений), маркер даёт только числитель
-- (реализованный эффект). См. docs/effect_metrics.md §9.
-- ============================================================================

CREATE OR REPLACE VIEW effect_metrics AS
    WITH marked AS (
        SELECT order_num, niche, order_date, rec_kind, qty,
               CASE
                   WHEN rec_origin_order IS NULL OR rec_origin_order = order_num
                       THEN 'Сценарий №1'
                   ELSE 'Сценарий №2'
               END AS scenario
        FROM order_lines
        WHERE rec_source
    )
    SELECT scenario,
           rec_kind,
           niche,
           date_trunc('month', order_date)::date AS period_month,
           count(DISTINCT order_num) AS orders_with_effect,   -- заказов с эффектом
           count(*)                  AS positions_added,      -- добавленных позиций
           round(sum(qty), 3)        AS added_tonnage         -- прирост тоннажа
    FROM marked
    GROUP BY scenario, rec_kind, niche, date_trunc('month', order_date);

-- Денежный эффект: added_tonnage * удельная EBITDA (₽/тн) — TODO справочник.
