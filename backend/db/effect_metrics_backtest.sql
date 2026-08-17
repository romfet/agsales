-- ============================================================================
-- ИНТЕРИМ: приблизительная оценка эффекта (сценарий №2) БЭКТЕСТОМ по истории.
-- ============================================================================
-- ⚠️ ХРУПКО и НЕ основной путь. Робастный расчёт — по маркеру рекомендации
-- (backend/db/effect_metrics.sql, прямое чтение rec_source). Этот файл — только
-- на переходный период, пока 1С не начала проставлять маркер: даёт грубую оценку
-- «потолка» через матчинг+реплей (см. хрупкости в docs/effect_metrics.md).
-- Не подключать в прод-схему.
-- ============================================================================
-- Модель (docs/effect_metrics.md): заказ живёт 2 раб. дня, потом закрывается.
-- Для КАЖДОГО закрытого заказа-триггера:
--   1) реплеим нишевые рекомендации R = популярные в нише подгруппы (n3),
--      отсутствующие в этом заказе;
--   2) ищем НОВЫЙ заказ того же клиента ПОСЛЕ закрытия триггера, «переделку»
--      закрытого — по пересечению позиций (>= доли состава триггера);
--   3) дифф: позиции нового заказа, которых НЕ было в закрытом И которые были
--      рекомендованы → это AI-добавленные. Их тоннаж = реализованный эффект.
-- Конверсия = «новый заказ создан» (статуса отгрузки в 1С нет). Гранулярность N3.
--
-- НЕ подключено к backend/db/schema.py / MATERIALIZED_VIEWS — формулы к согласованию.
--
-- Упрощения/параметры (осознанные, см. §9 спеки — согласовать):
--   lifetime_days   = 2   -- жизнь заказа до закрытия (календарные ≈ рабочие)
--   match_window    = 5   -- сколько дней после закрытия искать «переделку»
--   min_overlap_frac= 0.5 -- новый заказ должен повторять >= 50% позиций закрытого
--   min_niche_pct   = 5   -- порог рекомендации (как в analyzer.py)
-- Также: нишевая популярность — all-time (lookahead-bias, TODO point-in-time);
-- окно — в календарных днях (TODO произв. календарь).
-- ============================================================================

-- 1) Заголовки заказов: одна строка на order_num --------------------------------
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

-- 2) Состав заказа (подгруппы n3) -----------------------------------------------
CREATE MATERIALIZED VIEW IF NOT EXISTS em_order_n3 AS
    SELECT order_num, n3_id FROM order_lines GROUP BY order_num, n3_id
    WITH NO DATA;
CREATE UNIQUE INDEX IF NOT EXISTS ux_em_order_n3 ON em_order_n3 (order_num, n3_id);
CREATE INDEX IF NOT EXISTS ix_em_order_n3_n3 ON em_order_n3 (n3_id);

-- 3) Размер заказа (число подгрупп) — для порога пересечения ---------------------
CREATE MATERIALIZED VIEW IF NOT EXISTS em_order_size AS
    SELECT order_num, count(*) AS n3_count FROM em_order_n3 GROUP BY order_num
    WITH NO DATA;
CREATE UNIQUE INDEX IF NOT EXISTS ux_em_order_size ON em_order_size (order_num);

-- 4) Нишевая популярность на уровне N3 (all-time; см. упрощение) -----------------
CREATE MATERIALIZED VIEW IF NOT EXISTS em_niche_n3 AS
    WITH nc AS (
        SELECT niche, n3_id, count(DISTINCT client_id) AS client_count
        FROM order_lines WHERE niche IS NOT NULL GROUP BY niche, n3_id
    ),
    nt AS (
        SELECT niche, count(DISTINCT client_id) AS total_clients
        FROM order_lines WHERE niche IS NOT NULL GROUP BY niche
    )
    SELECT nc.niche, nc.n3_id,
           round(nc.client_count::numeric / NULLIF(nt.total_clients, 0) * 100, 1) AS niche_pct
    FROM nc JOIN nt USING (niche)
    WITH NO DATA;
CREATE UNIQUE INDEX IF NOT EXISTS ux_em_niche_n3 ON em_niche_n3 (niche, n3_id);

-- 5) Рекомендации на заказ-триггер (реплей analyze_niche) ------------------------
--    Популярные в нише n3 (>= порога), отсутствующие в самом заказе.
CREATE MATERIALIZED VIEW IF NOT EXISTS em_recs AS
    SELECT o.order_num  AS trig_order,
           o.client_id,
           o.niche,
           o.order_date AS trig_date,
           np.n3_id
    FROM em_order_header o
    JOIN em_niche_n3 np ON np.niche = o.niche AND np.niche_pct >= 5   -- min_niche_pct
    WHERE o.order_date IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM em_order_n3 oo
          WHERE oo.order_num = o.order_num AND oo.n3_id = np.n3_id
      )
    WITH NO DATA;
CREATE UNIQUE INDEX IF NOT EXISTS ux_em_recs ON em_recs (trig_order, n3_id);
CREATE INDEX IF NOT EXISTS ix_em_recs_slice ON em_recs (niche, trig_date);

-- 6) Строгий матч + дифф: AI-добавленные позиции в «переделке» -------------------
CREATE MATERIALIZED VIEW IF NOT EXISTS em_effect_s2 AS
    WITH cand AS (                                   -- кандидаты «закрытый ↔ новый»
        SELECT o1.order_num  AS trig,
               o2.order_num  AS newo,
               o1.niche,
               o1.order_date AS trig_date,
               o2.order_date AS new_date,
               count(*)      AS overlap              -- общих подгрупп
        FROM em_order_header o1
        JOIN em_order_header o2
          ON o2.client_id  = o1.client_id
         AND o2.order_num <> o1.order_num
         AND o1.order_date IS NOT NULL
         AND o2.order_date IS NOT NULL
         AND o2.order_date >  o1.order_date + 2       -- после закрытия (lifetime_days)
         AND o2.order_date <= o1.order_date + 2 + 5   -- + match_window
        JOIN em_order_n3 n1 ON n1.order_num = o1.order_num
        JOIN em_order_n3 n2 ON n2.order_num = o2.order_num AND n2.n3_id = n1.n3_id
        GROUP BY o1.order_num, o2.order_num, o1.niche, o1.order_date, o2.order_date
    ),
    best AS (                                        -- лучший матч на триггер
        SELECT DISTINCT ON (c.trig)
               c.trig, c.newo, c.niche, c.trig_date
        FROM cand c
        JOIN em_order_size s ON s.order_num = c.trig
        WHERE c.overlap >= GREATEST(1, ceil(0.5 * s.n3_count))   -- min_overlap_frac
        ORDER BY c.trig, c.overlap DESC, c.new_date ASC, c.newo
    )
    SELECT b.trig AS trig_order, b.newo, b.niche, b.trig_date,
           n2.n3_id,
           ( SELECT round(sum(ol.qty), 3) FROM order_lines ol
             WHERE ol.order_num = b.newo AND ol.n3_id = n2.n3_id ) AS added_qty
    FROM best b
    JOIN em_order_n3 n2 ON n2.order_num = b.newo
    JOIN em_recs r ON r.trig_order = b.trig AND r.n3_id = n2.n3_id      -- позиция рекомендована
    WHERE NOT EXISTS (                                                  -- и её не было в закрытом
        SELECT 1 FROM em_order_n3 n1
        WHERE n1.order_num = b.trig AND n1.n3_id = n2.n3_id
    )
    WITH NO DATA;
CREATE UNIQUE INDEX IF NOT EXISTS ux_em_effect_s2 ON em_effect_s2 (trig_order, n3_id);
CREATE INDEX IF NOT EXISTS ix_em_effect_s2_slice ON em_effect_s2 (niche, trig_date);

-- 7) Витрина для PowerBI: ниша × месяц ------------------------------------------
--    analyzed  = закрытые заказы, где ассистент дал ≥1 рекомендацию;
--    converted = из них — где нашлась «переделка» с AI-добавленной позицией.
CREATE OR REPLACE VIEW effect_metrics AS
    WITH analyzed AS (
        SELECT niche, date_trunc('month', trig_date)::date AS period_month,
               count(DISTINCT trig_order) AS analyzed_orders
        FROM em_recs GROUP BY 1, 2
    ),
    converted AS (
        SELECT niche, date_trunc('month', trig_date)::date AS period_month,
               count(DISTINCT trig_order) AS converted_orders,
               round(sum(added_qty), 3)   AS realized_tonnage
        FROM em_effect_s2 GROUP BY 1, 2
    )
    SELECT 'Сценарий №2'::text            AS scenario,
           a.niche,
           a.period_month,
           a.analyzed_orders,
           COALESCE(c.converted_orders, 0) AS converted_orders,
           round(COALESCE(c.converted_orders, 0)::numeric
                 / NULLIF(a.analyzed_orders, 0) * 100, 1) AS conversion_pct,
           COALESCE(c.realized_tonnage, 0) AS realized_tonnage
    FROM analyzed a
    LEFT JOIN converted c
      ON c.niche = a.niche AND c.period_month = a.period_month;

-- Денежный эффект (§6): realized_tonnage * удельная EBITDA (₽/тн) — TODO справочник.
-- Порядок REFRESH (для MATERIALIZED_VIEWS): em_order_header, em_order_n3,
-- em_order_size, em_niche_n3, em_recs, em_effect_s2. effect_metrics — обычный VIEW.
