-- Миграция 0002 — витрина эффекта на грейне N3 + резолв ниши из заказов∪отгрузок.
-- Аддитивно/безопасно: пересобирает только матвью effect_funnel (DROP+CREATE) и
-- добавляет client_niche. Базовые таблицы и данные 1С не трогаются.
--
-- Зачем:
-- * Витрина на грейне (order_id, n3_id) — рекомендации и анализ идут по подгруппе
--   (N3); журнал рекомендаций тоже N3. Единый грейн → атрибуция A = журнал ⨝ витрина
--   по (order_id, n3_id) сходится (раньше витрина была по N4 и джойн не совпадал).
-- * Флаги позиции разведены по смыслу: is_new_position (подгруппы не было → появилась)
--   и is_qty_increased (была и выросла). qty_added — прирост тоннажа (включая рост).
-- * period_month — месяц ЗАКАЗА (по версиям); дата отгрузки берётся в период только
--   если у заказа нет версий (пришёл лишь через отгрузку).
-- * client_niche — ниша клиента как мода по заказам∪отгрузкам, чтобы ниша резолвилась
--   даже до появления отгрузок (иначе анализ по нише молча отключался).

-- Ниша клиента (мода) из заказов ∪ отгрузок — резолв ниши в /analyze.
CREATE MATERIALIZED VIEW IF NOT EXISTS client_niche AS
    SELECT client_id, mode() WITHIN GROUP (ORDER BY niche) AS niche
    FROM (
        SELECT client_id, niche FROM order_lines    WHERE niche IS NOT NULL
        UNION ALL
        SELECT client_id, niche FROM shipment_lines WHERE niche IS NOT NULL
    ) z
    GROUP BY client_id
    WITH NO DATA;
CREATE UNIQUE INDEX IF NOT EXISTS ux_client_niche ON client_niche (client_id);

-- Витрина воронки эффекта (v2). Грейн: (order_id, n3_id). Только GUID + меры —
-- наименования BI резолвит из справочников 1С. Источники: версии заказа (первая=O /
-- последняя=F по version_seq) + отгрузки (факт). BI: Σ qty_original → qty_final →
-- qty_added → qty_shipped, срез по client/niche/n3/месяц; атрибуция A — джойн с
-- recommendation_log по (order_id, n3_id).
DROP MATERIALIZED VIEW IF EXISTS effect_funnel;
CREATE MATERIALIZED VIEW IF NOT EXISTS effect_funnel AS
    WITH bounds AS (
        SELECT order_id, min(version_seq) AS first_seq, max(version_seq) AS last_seq
        FROM order_line_versions GROUP BY order_id
    ),
    o AS (   -- исходная версия (O), тоннаж по подгруппе
        SELECT v.order_id, v.n3_id, sum(v.qty) AS qty
        FROM order_line_versions v JOIN bounds b
          ON b.order_id = v.order_id AND v.version_seq = b.first_seq
        GROUP BY v.order_id, v.n3_id
    ),
    f AS (   -- финальная версия (F)
        SELECT v.order_id, v.n3_id, sum(v.qty) AS qty
        FROM order_line_versions v JOIN bounds b
          ON b.order_id = v.order_id AND v.version_seq = b.last_seq
        GROUP BY v.order_id, v.n3_id
    ),
    s AS (   -- отгружено по заказу+подгруппе (m2m по order_id)
        SELECT order_id, n3_id, sum(qty) AS qty
        FROM shipment_lines GROUP BY order_id, n3_id
    ),
    keys AS (
        SELECT order_id, n3_id FROM o
        UNION SELECT order_id, n3_id FROM f
        UNION SELECT order_id, n3_id FROM s
    ),
    dim AS (  -- измерения на заказ; order_date и ship_date РАЗДЕЛЕНЫ (период — по заказу)
        SELECT order_id,
               max(client_id)  AS client_id,
               max(niche)      AS niche,
               max(order_date) AS order_date,   -- NULL, если у заказа нет версий
               max(ship_date)  AS ship_date
        FROM (
            SELECT order_id, client_id, niche, order_date, NULL::date AS ship_date
            FROM order_line_versions
            UNION ALL
            SELECT order_id, client_id, niche, NULL::date AS order_date, shipment_date AS ship_date
            FROM shipment_lines
        ) z GROUP BY order_id
    )
    SELECT k.order_id, k.n3_id,
           d.client_id, d.niche,
           date_trunc('month', COALESCE(d.order_date, d.ship_date))::date AS period_month,
           round(COALESCE(o.qty, 0), 3)                                   AS qty_original,
           round(COALESCE(f.qty, 0), 3)                                   AS qty_final,
           round(GREATEST(COALESCE(f.qty,0) - COALESCE(o.qty,0), 0), 3)   AS qty_added,
           round(COALESCE(s.qty, 0), 3)                                   AS qty_shipped,
           (COALESCE(o.qty,0) > 0)::int                                   AS is_original,
           (COALESCE(f.qty,0) > 0)::int                                   AS is_final,
           (COALESCE(o.qty,0) = 0 AND COALESCE(f.qty,0) > 0)::int         AS is_new_position,
           (COALESCE(f.qty,0) > COALESCE(o.qty,0) AND COALESCE(o.qty,0) > 0)::int AS is_qty_increased,
           (COALESCE(s.qty,0) > 0)::int                                   AS is_shipped
    FROM keys k
    LEFT JOIN o   ON o.order_id = k.order_id AND o.n3_id = k.n3_id
    LEFT JOIN f   ON f.order_id = k.order_id AND f.n3_id = k.n3_id
    LEFT JOIN s   ON s.order_id = k.order_id AND s.n3_id = k.n3_id
    LEFT JOIN dim d ON d.order_id = k.order_id
    WITH NO DATA;
CREATE UNIQUE INDEX IF NOT EXISTS ux_effect_funnel ON effect_funnel (order_id, n3_id);
CREATE INDEX IF NOT EXISTS ix_effect_funnel_slice ON effect_funnel (niche, period_month);
