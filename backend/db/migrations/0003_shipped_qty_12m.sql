-- Миграция 0003 — годовой тоннаж отгрузок в профилях (для shipped_qty).
-- Добавляет колонку total_qty_12m = Σ отгружено за ПОСЛЕДНИЕ 12 месяцев (скользящее
-- окно от CURRENT_DATE на момент пересборки матвью). Остальная математика (частота,
-- счётчики, avg_qty_*) — как была, по всей истории. Матвью пересоздаются (в матвью
-- нельзя ADD COLUMN); данные не трогаются, base-таблицы не меняются.

DROP MATERIALIZED VIEW IF EXISTS client_profile_n3;
CREATE MATERIALIZED VIEW IF NOT EXISTS client_profile_n3 AS
    WITH per AS (
        SELECT client_id, n3_id,
               count(DISTINCT shipment_id) AS order_count,
               sum(qty)                  AS total_qty,
               sum(qty) FILTER (WHERE shipment_date >= CURRENT_DATE - INTERVAL '1 year')
                                         AS total_qty_12m
        FROM shipment_lines GROUP BY client_id, n3_id
    ),
    rep AS (
        SELECT DISTINCT ON (client_id, n3_id) client_id, n3_id, n4_id AS rep_n4_id
        FROM (
            SELECT client_id, n3_id, n4_id, count(DISTINCT shipment_id) AS n4_orders
            FROM shipment_lines GROUP BY client_id, n3_id, n4_id
        ) z
        ORDER BY client_id, n3_id, n4_orders DESC, n4_id
    )
    SELECT p.client_id, p.n3_id, r.rep_n4_id,
           p.order_count, p.total_qty, COALESCE(p.total_qty_12m, 0) AS total_qty_12m, d.total_orders,
           round(p.order_count::numeric / NULLIF(d.total_orders, 0) * 100, 1) AS frequency_pct,
           round(p.total_qty / NULLIF(p.order_count, 0), 3)                   AS avg_qty_per_order
    FROM per p
    JOIN client_dim d USING (client_id)
    JOIN rep r USING (client_id, n3_id)
    WITH NO DATA;
CREATE UNIQUE INDEX IF NOT EXISTS ux_client_profile_n3 ON client_profile_n3 (client_id, n3_id);
CREATE INDEX IF NOT EXISTS ix_client_profile_n3_client ON client_profile_n3 (client_id);

DROP MATERIALIZED VIEW IF EXISTS niche_profile_n3;
CREATE MATERIALIZED VIEW IF NOT EXISTS niche_profile_n3 AS
    WITH per AS (
        SELECT niche, n3_id,
               count(DISTINCT client_id) AS client_count,
               count(DISTINCT shipment_id) AS order_count,
               sum(qty)                  AS total_qty,
               sum(qty) FILTER (WHERE shipment_date >= CURRENT_DATE - INTERVAL '1 year')
                                         AS total_qty_12m
        FROM shipment_lines WHERE niche IS NOT NULL GROUP BY niche, n3_id
    ),
    rep AS (
        SELECT DISTINCT ON (niche, n3_id) niche, n3_id, n4_id AS rep_n4_id
        FROM (
            SELECT niche, n3_id, n4_id, count(DISTINCT client_id) AS n4_clients
            FROM shipment_lines WHERE niche IS NOT NULL GROUP BY niche, n3_id, n4_id
        ) z
        ORDER BY niche, n3_id, n4_clients DESC, n4_id
    )
    SELECT p.niche, p.n3_id, r.rep_n4_id,
           p.client_count, p.order_count, p.total_qty, COALESCE(p.total_qty_12m, 0) AS total_qty_12m,
           nd.total_clients_in_niche, nd.total_orders_in_niche,
           round(p.client_count::numeric / NULLIF(nd.total_clients_in_niche, 0) * 100, 1) AS niche_pct,
           round(p.order_count::numeric  / NULLIF(nd.total_orders_in_niche, 0) * 100, 1) AS niche_freq_pct,
           round(p.total_qty / NULLIF(p.client_count, 0), 3)                              AS avg_qty_per_client
    FROM per p
    JOIN niche_dim nd USING (niche)
    JOIN rep r USING (niche, n3_id)
    WITH NO DATA;
CREATE UNIQUE INDEX IF NOT EXISTS ux_niche_profile_n3 ON niche_profile_n3 (niche, n3_id);
CREATE INDEX IF NOT EXISTS ix_niche_profile_n3_niche ON niche_profile_n3 (niche);
