-- Миграция 0004 — разведение счётчиков ЗАКАЗОВ и ОТГРУЗОК.
--
-- Было: order_count / total_orders = count(DISTINCT shipment_id) — то есть счёт по
-- ОТГРУЗКАМ (РТУ), хотя поля называются «order». Заказ↔отгрузка — m2m, и один заказ
-- штатно уходит несколькими частичными РТУ → частота раздувалась у позиций, что чаще
-- едут догрузками, а не чаще заказываются.
--
-- Стало (единица счёта — заказ, всё по-прежнему из shipment_lines = реализованная
-- история; заказ без единой РТУ в аналитику не входит, домен сохранён):
--   order_count / total_orders            = count(DISTINCT order_id)     — по ЗАКАЗАМ
--   frequency_pct / niche_freq_pct        = order_count / total_orders   — доля ЗАКАЗОВ
--   avg_qty_per_order                     = total_qty / order_count      — теперь на заказ
-- Рядом добавлены честно названные меры по отгрузкам (контракт не теряет информацию):
--   shipment_count                        = count(DISTINCT shipment_id)  — РТУ с подгруппой
--   total_shipments / total_shipments_in_niche = count(DISTINCT shipment_id) на клиента/нишу
--
-- Матвью пересоздаются (в матвью нельзя ADD COLUMN); base-таблицы и данные 1С не
-- трогаются. Профили зависят от *_dim (JOIN), поэтому дропаем профили раньше dim.

DROP MATERIALIZED VIEW IF EXISTS client_profile_n3;
DROP MATERIALIZED VIEW IF EXISTS niche_profile_n3;
DROP MATERIALIZED VIEW IF EXISTS client_dim;
DROP MATERIALIZED VIEW IF EXISTS niche_dim;

-- Итоги по клиенту: total_orders — РАЗНЫЕ заказы, total_shipments — РАЗНЫЕ отгрузки.
CREATE MATERIALIZED VIEW IF NOT EXISTS client_dim AS
    SELECT client_id,
           max(client_name)                     AS client_name,
           mode() WITHIN GROUP (ORDER BY niche)  AS niche,
           count(DISTINCT order_id)              AS total_orders,
           count(DISTINCT shipment_id)           AS total_shipments
    FROM shipment_lines GROUP BY client_id WITH NO DATA;
CREATE UNIQUE INDEX IF NOT EXISTS ux_client_dim ON client_dim (client_id);

-- Итоги по нише: заказы/отгрузки — РАЗНЫЕ (без задвоения по SKU/строкам).
CREATE MATERIALIZED VIEW IF NOT EXISTS niche_dim AS
    SELECT niche,
           count(DISTINCT client_id)    AS total_clients_in_niche,
           count(DISTINCT order_id)      AS total_orders_in_niche,
           count(DISTINCT shipment_id)   AS total_shipments_in_niche
    FROM shipment_lines WHERE niche IS NOT NULL GROUP BY niche WITH NO DATA;
CREATE UNIQUE INDEX IF NOT EXISTS ux_niche_dim ON niche_dim (niche);

-- Профиль клиента на N3: order_count — заказы с подгруппой, shipment_count — отгрузки;
-- rep_n4_id — представительный товар (самый частый у клиента ПО ЗАКАЗАМ).
CREATE MATERIALIZED VIEW IF NOT EXISTS client_profile_n3 AS
    WITH per AS (
        SELECT client_id, n3_id,
               count(DISTINCT order_id)    AS order_count,
               count(DISTINCT shipment_id) AS shipment_count,
               sum(qty)                    AS total_qty,
               sum(qty) FILTER (WHERE shipment_date >= CURRENT_DATE - INTERVAL '1 year')
                                           AS total_qty_12m
        FROM shipment_lines GROUP BY client_id, n3_id
    ),
    rep AS (
        SELECT DISTINCT ON (client_id, n3_id) client_id, n3_id, n4_id AS rep_n4_id
        FROM (
            SELECT client_id, n3_id, n4_id, count(DISTINCT order_id) AS n4_orders
            FROM shipment_lines GROUP BY client_id, n3_id, n4_id
        ) z
        ORDER BY client_id, n3_id, n4_orders DESC, n4_id
    )
    SELECT p.client_id, p.n3_id, r.rep_n4_id,
           p.order_count, p.shipment_count, p.total_qty,
           COALESCE(p.total_qty_12m, 0) AS total_qty_12m,
           d.total_orders, d.total_shipments,
           round(p.order_count::numeric / NULLIF(d.total_orders, 0) * 100, 1) AS frequency_pct,
           round(p.total_qty / NULLIF(p.order_count, 0), 3)                   AS avg_qty_per_order
    FROM per p
    JOIN client_dim d USING (client_id)
    JOIN rep r USING (client_id, n3_id)
    WITH NO DATA;
CREATE UNIQUE INDEX IF NOT EXISTS ux_client_profile_n3 ON client_profile_n3 (client_id, n3_id);
CREATE INDEX IF NOT EXISTS ix_client_profile_n3_client ON client_profile_n3 (client_id);

-- Нишевый профиль на N3: order_count/shipment_count — заказы/отгрузки в нише с подгруппой;
-- rep_n4_id — самый популярный в нише товар подгруппы (по числу клиентов).
CREATE MATERIALIZED VIEW IF NOT EXISTS niche_profile_n3 AS
    WITH per AS (
        SELECT niche, n3_id,
               count(DISTINCT client_id)   AS client_count,
               count(DISTINCT order_id)     AS order_count,
               count(DISTINCT shipment_id)  AS shipment_count,
               sum(qty)                     AS total_qty,
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
           p.client_count, p.order_count, p.shipment_count, p.total_qty,
           COALESCE(p.total_qty_12m, 0) AS total_qty_12m,
           nd.total_clients_in_niche, nd.total_orders_in_niche, nd.total_shipments_in_niche,
           round(p.client_count::numeric / NULLIF(nd.total_clients_in_niche, 0) * 100, 1) AS niche_pct,
           round(p.order_count::numeric  / NULLIF(nd.total_orders_in_niche, 0) * 100, 1)  AS niche_freq_pct,
           round(p.total_qty / NULLIF(p.client_count, 0), 3)                              AS avg_qty_per_client
    FROM per p
    JOIN niche_dim nd USING (niche)
    JOIN rep r USING (niche, n3_id)
    WITH NO DATA;
CREATE UNIQUE INDEX IF NOT EXISTS ux_niche_profile_n3 ON niche_profile_n3 (niche, n3_id);
CREATE INDEX IF NOT EXISTS ix_niche_profile_n3_niche ON niche_profile_n3 (niche);
