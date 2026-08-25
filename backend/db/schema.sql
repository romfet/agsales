-- Единая схема БД (без миграций). Идемпотентна: применяется на старте контейнера.
-- Идентичность по GUID: client_id / n3_id / n4_id. Имён товара/подгруппы нет
-- (1С резолвит их у себя по GUID). niche = Отрасль, заказ = order_num.
-- Изменение схемы = пересоздать том БД (docker compose down -v), не ALTER.

CREATE TABLE IF NOT EXISTS order_lines (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id    text NOT NULL,          -- GUID заказа из 1С — канонический ключ
    order_num   text NOT NULL,          -- номер заказа (отображение)
    order_date  date,
    client_id   text NOT NULL,
    client_name text,
    niche       text,
    n3_id       text NOT NULL,
    n4_id       text NOT NULL,
    qty         numeric NOT NULL,
    -- Маркер рекомендации (заполняет 1С при принятии рекомендации ИИ):
    --   rec_source       — позиция добавлена из принятой рекомендации;
    --   rec_kind         — тип: 'forgotten' | 'niche';
    --   rec_origin_order — order_num, в котором позиция была рекомендована
    --                      (= order_num → сценарий №1; иначе → сценарий №2).
    rec_source       boolean NOT NULL DEFAULT false,
    rec_kind         text,
    rec_origin_order text
);
CREATE INDEX IF NOT EXISTS ix_order_lines_rec_source ON order_lines (rec_source) WHERE rec_source;
CREATE INDEX IF NOT EXISTS ix_order_lines_order_id  ON order_lines (order_id);
CREATE INDEX IF NOT EXISTS ix_order_lines_order_num ON order_lines (order_num);
CREATE INDEX IF NOT EXISTS ix_order_lines_client_id ON order_lines (client_id);
CREATE INDEX IF NOT EXISTS ix_order_lines_niche     ON order_lines (niche);

-- Версии заказа (append-only). Каждая ЗАГРУЗКА, изменившая заказ, пишет сюда его
-- новый снимок с монотонным version_seq (общий на всю пачку). Дискриминатор версии
-- — version_seq (НЕ ingested_at: таймстемпы двух загрузок могут совпасть). Снимок
-- пишется только если content_hash отличается от предыдущего (без задвоения
-- неизменных заказов). min/max version_seq заказа = исходная/финальная версия.
CREATE SEQUENCE IF NOT EXISTS order_version_seq;
CREATE TABLE IF NOT EXISTS order_line_versions (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    version_seq  bigint NOT NULL,        -- монотонный номер версии (на пачку загрузки)
    ingested_at  timestamptz NOT NULL DEFAULT now(),
    content_hash text,                   -- хэш состава заказа (для дедупа неизменных)
    order_id     text NOT NULL,          -- GUID заказа из 1С
    order_num    text NOT NULL,
    order_date   date,
    client_id    text NOT NULL,
    client_name  text,
    niche        text,
    n3_id        text NOT NULL,
    n4_id        text NOT NULL,
    qty          numeric NOT NULL,
    rec_source       boolean NOT NULL DEFAULT false,
    rec_kind         text,
    rec_origin_order text
);
CREATE INDEX IF NOT EXISTS ix_olv_order ON order_line_versions (order_id, version_seq);

-- Отгрузки (факт отгруженного товара). Контракт как у заказа + GUID заказа для
-- линка (заказ↔отгрузка — many-to-many). Ключ — shipment_id (GUID из 1С). Upsert
-- по shipment_id (перезалив заменяет строки отгрузки), удалений нет. qty =
-- фактически отгружено. Источник для рекомендаций (Фаза 3) и «отгружено» в воронке.
CREATE TABLE IF NOT EXISTS shipment_lines (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    shipment_id   text NOT NULL,       -- GUID отгрузки из 1С — ключ
    shipment_num  text,                -- номер документа отгрузки (отображение)
    shipment_date date,
    order_id      text NOT NULL,       -- GUID заказа (линк, m2m)
    client_id     text NOT NULL,
    client_name   text,
    niche         text,
    n3_id         text NOT NULL,
    n4_id         text NOT NULL,
    qty           numeric NOT NULL      -- фактически отгружено
);
CREATE INDEX IF NOT EXISTS ix_shipment_lines_shipment ON shipment_lines (shipment_id);
CREATE INDEX IF NOT EXISTS ix_shipment_lines_order    ON shipment_lines (order_id);
CREATE INDEX IF NOT EXISTS ix_shipment_lines_client   ON shipment_lines (client_id);
CREATE INDEX IF NOT EXISTS ix_shipment_lines_niche    ON shipment_lines (niche);

CREATE MATERIALIZED VIEW IF NOT EXISTS client_dim AS
    SELECT client_id,
           max(client_name)                      AS client_name,
           mode() WITHIN GROUP (ORDER BY niche)  AS niche,
           count(DISTINCT shipment_id)             AS total_orders
    FROM shipment_lines GROUP BY client_id WITH NO DATA;
CREATE UNIQUE INDEX IF NOT EXISTS ux_client_dim ON client_dim (client_id);

CREATE MATERIALIZED VIEW IF NOT EXISTS niche_dim AS
    SELECT niche,
           count(DISTINCT client_id)  AS total_clients_in_niche,
           count(DISTINCT shipment_id)  AS total_orders_in_niche
    FROM shipment_lines WHERE niche IS NOT NULL GROUP BY niche WITH NO DATA;
CREATE UNIQUE INDEX IF NOT EXISTS ux_niche_dim ON niche_dim (niche);

-- Каталог n4→n3 для резолва позиций черновика в /analyze. Строится из ЗАКАЗОВ ∪
-- ОТГРУЗОК, чтобы резолвился и товар, который заказан, но ещё не отгружён (иначе
-- строка черновика молча выпадала бы и подгруппа ложно считалась «забытой»).
CREATE MATERIALIZED VIEW IF NOT EXISTS products AS
    SELECT n4_id, max(n3_id) AS n3_id
    FROM (
        SELECT n4_id, n3_id FROM order_lines
        UNION ALL
        SELECT n4_id, n3_id FROM shipment_lines
    ) u GROUP BY n4_id WITH NO DATA;
CREATE UNIQUE INDEX IF NOT EXISTS ux_products ON products (n4_id);

-- Профили на уровне N3 (подгруппа) — источник рекомендаций (анализ идёт по N3).
-- Строятся из ОТГРУЗОК (shipment_lines): единица события — shipment_id (реализ.
-- закупка). Одна строка на (клиент, n3): order_count — число РАЗНЫХ отгрузок с
-- подгруппой (без задвоения по SKU), rep_n4_id — представительный товар подгруппы
-- (частейший у клиента по отгрузкам).
CREATE MATERIALIZED VIEW IF NOT EXISTS client_profile_n3 AS
    WITH per AS (
        SELECT client_id, n3_id,
               count(DISTINCT shipment_id) AS order_count,
               sum(qty)                  AS total_qty
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
           p.order_count, p.total_qty, d.total_orders,
           round(p.order_count::numeric / NULLIF(d.total_orders, 0) * 100, 1) AS frequency_pct,
           round(p.total_qty / NULLIF(p.order_count, 0), 3)                   AS avg_qty_per_order
    FROM per p
    JOIN client_dim d USING (client_id)
    JOIN rep r USING (client_id, n3_id)
    WITH NO DATA;
CREATE UNIQUE INDEX IF NOT EXISTS ux_client_profile_n3 ON client_profile_n3 (client_id, n3_id);
CREATE INDEX IF NOT EXISTS ix_client_profile_n3_client ON client_profile_n3 (client_id);

-- Нишевый профиль на уровне N3 — «что берут в нише» (из отгрузок). Одна строка на
-- (niche, n3): client_count/order_count — РАЗНЫЕ клиенты/отгрузки (без задвоения по
-- SKU), rep_n4_id — самый популярный в нише товар подгруппы.
CREATE MATERIALIZED VIEW IF NOT EXISTS niche_profile_n3 AS
    WITH per AS (
        SELECT niche, n3_id,
               count(DISTINCT client_id) AS client_count,
               count(DISTINCT shipment_id) AS order_count,
               sum(qty)                  AS total_qty
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
           p.client_count, p.order_count, p.total_qty,
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
