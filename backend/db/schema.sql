-- Единая схема БД (без миграций). Идемпотентна: применяется на старте контейнера.
-- Идентичность по GUID: client_id / n3_id / n4_id. Имён товара/подгруппы нет
-- (1С резолвит их у себя по GUID). niche = Отрасль, заказ = order_num.
-- Изменение схемы = пересоздать том БД (docker compose down -v), не ALTER.

CREATE TABLE IF NOT EXISTS order_lines (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_num   text NOT NULL,
    order_date  date,
    client_id   text NOT NULL,
    client_name text,
    niche       text,
    n3_id       text NOT NULL,
    n4_id       text NOT NULL,
    qty         numeric NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_order_lines_order_num ON order_lines (order_num);
CREATE INDEX IF NOT EXISTS ix_order_lines_client_id ON order_lines (client_id);
CREATE INDEX IF NOT EXISTS ix_order_lines_niche     ON order_lines (niche);

CREATE MATERIALIZED VIEW IF NOT EXISTS client_dim AS
    SELECT client_id,
           max(client_name)                      AS client_name,
           mode() WITHIN GROUP (ORDER BY niche)  AS niche,
           count(DISTINCT order_num)             AS total_orders
    FROM order_lines GROUP BY client_id WITH NO DATA;
CREATE UNIQUE INDEX IF NOT EXISTS ux_client_dim ON client_dim (client_id);

CREATE MATERIALIZED VIEW IF NOT EXISTS niche_dim AS
    SELECT niche,
           count(DISTINCT client_id)  AS total_clients_in_niche,
           count(DISTINCT order_num)  AS total_orders_in_niche
    FROM order_lines WHERE niche IS NOT NULL GROUP BY niche WITH NO DATA;
CREATE UNIQUE INDEX IF NOT EXISTS ux_niche_dim ON niche_dim (niche);

CREATE MATERIALIZED VIEW IF NOT EXISTS products AS
    SELECT n4_id, max(n3_id) AS n3_id
    FROM order_lines GROUP BY n4_id WITH NO DATA;
CREATE UNIQUE INDEX IF NOT EXISTS ux_products ON products (n4_id);

CREATE MATERIALIZED VIEW IF NOT EXISTS client_profile_n4 AS
    WITH per AS (
        SELECT client_id, n4_id,
               max(n3_id)                AS n3_id,
               count(DISTINCT order_num) AS order_count,
               sum(qty)                  AS total_qty
        FROM order_lines GROUP BY client_id, n4_id
    )
    SELECT p.client_id, p.n3_id, p.n4_id,
           p.order_count, p.total_qty, d.total_orders,
           round(p.order_count::numeric / NULLIF(d.total_orders, 0) * 100, 1) AS frequency_pct,
           round(p.total_qty / NULLIF(p.order_count, 0), 3)                   AS avg_qty_per_order
    FROM per p JOIN client_dim d USING (client_id)
    WITH NO DATA;
CREATE UNIQUE INDEX IF NOT EXISTS ux_client_profile_n4 ON client_profile_n4 (client_id, n4_id);
CREATE INDEX IF NOT EXISTS ix_client_profile_n4_client ON client_profile_n4 (client_id);

CREATE MATERIALIZED VIEW IF NOT EXISTS niche_profile_n4 AS
    WITH per AS (
        SELECT niche, n4_id,
               max(n3_id)                AS n3_id,
               count(DISTINCT client_id) AS client_count,
               count(DISTINCT order_num) AS order_count,
               sum(qty)                  AS total_qty
        FROM order_lines WHERE niche IS NOT NULL GROUP BY niche, n4_id
    )
    SELECT p.niche, p.n3_id, p.n4_id,
           p.client_count, p.order_count, p.total_qty,
           nd.total_clients_in_niche, nd.total_orders_in_niche,
           round(p.client_count::numeric / NULLIF(nd.total_clients_in_niche, 0) * 100, 1) AS niche_pct,
           round(p.order_count::numeric  / NULLIF(nd.total_orders_in_niche, 0) * 100, 1) AS niche_freq_pct,
           round(p.total_qty / NULLIF(p.client_count, 0), 3)                              AS avg_qty_per_client
    FROM per p JOIN niche_dim nd USING (niche)
    WITH NO DATA;
CREATE UNIQUE INDEX IF NOT EXISTS ux_niche_profile_n4 ON niche_profile_n4 (niche, n4_id);
CREATE INDEX IF NOT EXISTS ix_niche_profile_n4_niche ON niche_profile_n4 (niche);
