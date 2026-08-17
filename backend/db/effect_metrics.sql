-- ============================================================================
-- Фаза A: бэктест эффекта ИИ-ассистента на истории order_lines. ЧЕРНОВИК.
-- ============================================================================
-- Спека: docs/effect_metrics.md (§0.1). Считаем СЦЕНАРИЙ №2 (новый заказ клиента
-- с рекомендованной позицией в «срок жизни рекомендации») — он полностью
-- воспроизводится из имеющихся данных, без правок контракта 1С.
--
-- Идея: рекомендатель детерминирован (analyze_niche — чистая функция), поэтому
-- для каждого исторического заказа реплеим нишевые рекомендации и проверяем, не
-- появилась ли рекомендованная подгруппа (n3_id) в ПОСЛЕДУЮЩЕМ заказе клиента
-- внутри окна. Гранулярность — N3 (как в анализаторе).
--
-- НЕ подключено к backend/db/schema.py / MATERIALIZED_VIEWS — обсуждаем формулы.
-- Чтобы интегрировать: перенести CREATE-ы в schema.sql и добавить имена матвью в
-- MATERIALIZED_VIEWS (в порядке ниже — уважает зависимости).
--
-- УПРОЩЕНИЯ (осознанные, см. §0.1 и §9 спеки):
--   1. Нишевую популярность берём за ВСЮ историю, не point-in-time → есть
--      lookahead-bias. TODO Фаза A+: считать профиль на момент заказа (< trig_date).
--   2. Окно — в КАЛЕНДАРНЫХ днях (:lifetime_days). «2 рабочих дня» ≈ 4 календарных
--      как ориентир. TODO: производственный календарь (§9).
--   3. Порог рекомендации MIN_NICHE_PCT=5 захардкожен (совпадает с analyzer.py).
--   4. Дедуп: если одна и та же рекомендация «сыграла» в нескольких заказах —
--      берём ПЕРВЫЙ заказ в окне; но один новый заказ может закрыть несколько
--      рекомендаций (тоннаж посчитается по каждой n3). См. §9 п.8.
--
-- Параметры (меняются правкой SQL + REFRESH; runtime-настройка — Фаза B):
--   lifetime_days = 4   -- ≈ «срок жизни рекомендации» 2 раб. дня
--   min_niche_pct = 5   -- MIN_NICHE_PCT
-- ============================================================================

-- 1) Заголовки заказов: одна строка на order_num (клиент, ниша, дата) -----------
CREATE MATERIALIZED VIEW IF NOT EXISTS em_order_header AS
    SELECT order_num,
           client_id,
           mode() WITHIN GROUP (ORDER BY niche) AS niche,
           min(order_date)                      AS order_date
    FROM order_lines
    GROUP BY order_num, client_id
    WITH NO DATA;
CREATE UNIQUE INDEX IF NOT EXISTS ux_em_order_header ON em_order_header (order_num);
CREATE INDEX IF NOT EXISTS ix_em_order_header_client ON em_order_header (client_id, order_date);

-- 2) Набор подгрупп (n3) в каждом заказе — для «позиция отсутствует в заказе» ----
CREATE MATERIALIZED VIEW IF NOT EXISTS em_order_n3 AS
    SELECT order_num, n3_id FROM order_lines GROUP BY order_num, n3_id
    WITH NO DATA;
CREATE UNIQUE INDEX IF NOT EXISTS ux_em_order_n3 ON em_order_n3 (order_num, n3_id);

-- 3) Нишевая популярность на уровне N3 (all-time; см. упрощение №1) --------------
CREATE MATERIALIZED VIEW IF NOT EXISTS em_niche_n3 AS
    WITH nc AS (
        SELECT niche, n3_id, count(DISTINCT client_id) AS client_count
        FROM order_lines WHERE niche IS NOT NULL GROUP BY niche, n3_id
    ),
    nt AS (
        SELECT niche, count(DISTINCT client_id) AS total_clients
        FROM order_lines WHERE niche IS NOT NULL GROUP BY niche
    )
    SELECT nc.niche, nc.n3_id, nc.client_count, nt.total_clients,
           round(nc.client_count::numeric / NULLIF(nt.total_clients, 0) * 100, 1) AS niche_pct
    FROM nc JOIN nt USING (niche)
    WITH NO DATA;
CREATE UNIQUE INDEX IF NOT EXISTS ux_em_niche_n3 ON em_niche_n3 (niche, n3_id);

-- 4) Когда клиент ВПЕРВЫЕ купил подгруппу — для флага «покупал раньше» -----------
CREATE MATERIALIZED VIEW IF NOT EXISTS em_client_n3_first AS
    SELECT client_id, n3_id, min(order_date) AS first_date
    FROM order_lines GROUP BY client_id, n3_id
    WITH NO DATA;
CREATE UNIQUE INDEX IF NOT EXISTS ux_em_client_n3_first ON em_client_n3_first (client_id, n3_id);

-- 5) Бэктест: (заказ-триггер × рекомендованная n3) + факт «сыграла ли» ----------
--    Реплей analyze_niche: нишевые n3 с niche_pct >= 5, отсутствующие в триггере.
--    hit_* != NULL, если рекомендованная n3 появилась в новом заказе клиента в окне.
CREATE MATERIALIZED VIEW IF NOT EXISTS effect_backtest_s2 AS
    WITH recs AS (
        SELECT o.order_num  AS trig_order,
               o.client_id,
               o.niche,
               o.order_date AS trig_date,
               np.n3_id,
               np.niche_pct,
               EXISTS (
                   SELECT 1 FROM em_client_n3_first b
                   WHERE b.client_id = o.client_id
                     AND b.n3_id     = np.n3_id
                     AND b.first_date < o.order_date
               ) AS client_bought_before
        FROM em_order_header o
        JOIN em_niche_n3 np
          ON np.niche = o.niche
         AND np.niche_pct >= 5                       -- MIN_NICHE_PCT
        WHERE o.order_date IS NOT NULL
          AND NOT EXISTS (                            -- n3 отсутствует в самом заказе
              SELECT 1 FROM em_order_n3 oo
              WHERE oo.order_num = o.order_num AND oo.n3_id = np.n3_id
          )
    )
    SELECT r.trig_order, r.client_id, r.niche, r.trig_date,
           r.n3_id, r.niche_pct, r.client_bought_before,
           h.hit_order, h.hit_date, h.realized_qty
    FROM recs r
    LEFT JOIN LATERAL (                               -- первый новый заказ в окне с этой n3
        SELECT ol.order_num AS hit_order,
               oh.order_date AS hit_date,
               round(sum(ol.qty), 3) AS realized_qty
        FROM order_lines ol
        JOIN em_order_header oh ON oh.order_num = ol.order_num
        WHERE ol.client_id  = r.client_id
          AND ol.n3_id      = r.n3_id
          AND ol.order_num <> r.trig_order
          AND oh.order_date >  r.trig_date
          AND oh.order_date <= r.trig_date + 4        -- lifetime_days (≈2 раб. дня)
        GROUP BY ol.order_num, oh.order_date
        ORDER BY oh.order_date ASC
        LIMIT 1
    ) h ON true
    WITH NO DATA;
CREATE UNIQUE INDEX IF NOT EXISTS ux_effect_backtest_s2 ON effect_backtest_s2 (trig_order, n3_id);
CREATE INDEX IF NOT EXISTS ix_effect_backtest_s2_slice ON effect_backtest_s2 (niche, trig_date);

-- 6) Витрина для PowerBI: агрегаты по нише × месяцу ----------------------------
--    Обычный VIEW поверх матвью (5) — пересчёта не требует.
--    analyzed_orders  = заказы, где ассистент «сработал» (была ≥1 рекомендация).
--    converted_orders = из них — где рекомендация «сыграла» (сценарий №2).
CREATE OR REPLACE VIEW effect_metrics AS
    SELECT
        'Сценарий №2'                                   AS scenario,
        niche,
        date_trunc('month', trig_date)::date            AS period_month,
        count(DISTINCT trig_order)                      AS analyzed_orders,
        count(DISTINCT trig_order)
            FILTER (WHERE hit_order IS NOT NULL)        AS converted_orders,
        round(
            count(DISTINCT trig_order) FILTER (WHERE hit_order IS NOT NULL)::numeric
            / NULLIF(count(DISTINCT trig_order), 0) * 100, 1
        )                                               AS conversion_pct,
        round(sum(realized_qty)
            FILTER (WHERE hit_order IS NOT NULL), 3)    AS realized_tonnage,
        count(*)                                        AS recommendations,
        count(*) FILTER (WHERE hit_order IS NOT NULL)   AS recommendations_hit
    FROM effect_backtest_s2
    GROUP BY niche, date_trunc('month', trig_date);

-- Денежный эффект (§6): realized_tonnage * удельная EBITDA (₽/тн).
-- TODO: JOIN к справочнику EBITDA, когда согласуют источник/гранулярность (§9 п.6).

-- Порядок REFRESH (для MATERIALIZED_VIEWS): em_order_header, em_order_n3,
-- em_niche_n3, em_client_n3_first, effect_backtest_s2. effect_metrics — обычный
-- VIEW, не рефрешится.
