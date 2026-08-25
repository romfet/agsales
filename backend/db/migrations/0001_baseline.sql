-- Миграция 0001 — базовая схема (baseline). Идемпотентна (CREATE … IF NOT EXISTS):
-- безопасно применяется и на уже наполненном стенде (объекты не пересоздаются).
-- Идентичность по GUID: client_id / niche / n3_id / n4_id / order_id / shipment_id.
-- Имён товара/подгруппы нет (1С резолвит их у себя по GUID). niche = Отрасль.
-- Последующие изменения схемы — НОВЫЙ файл db/migrations/NNNN_*.sql (ALTER/CREATE),
-- НЕ правка этого файла и НЕ пересоздание тома (реальные данные 1С должны выжить).

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
    qty         numeric NOT NULL
);
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
    qty          numeric NOT NULL
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

-- Журнал выданных рекомендаций (append-only). Пишется на каждый вызов /api/analyze:
-- одна строка на рекомендованную позицию. created_at = «ассистент сработал»,
-- order_id = заказ, по которому шёл анализ. Даёт когорту «с ассистентом» и что
-- именно рекомендовали (для атрибуции A/B/C в BI).
CREATE TABLE IF NOT EXISTS recommendation_log (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT now(),
    client_id  text NOT NULL,
    order_id   text,                -- заказ, по которому шёл анализ (может быть NULL)
    kind       text NOT NULL,       -- 'forgotten' | 'niche'
    n3_id      text NOT NULL,
    n4_id      text,
    priority   text
);
CREATE INDEX IF NOT EXISTS ix_reclog_client ON recommendation_log (client_id, created_at);
CREATE INDEX IF NOT EXISTS ix_reclog_order  ON recommendation_log (order_id);

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

-- Витрина воронки эффекта для BI (Фаза 4). Грейн: (order_id, n3_id, n4_id). Только
-- GUID + меры — наименования BI резолвит из справочников 1С. Источники: версии
-- заказа (первая=исходное O / последняя=финал F по version_seq) + отгрузки (факт).
-- Меры: тоннаж (original/final/added/shipped) + флаги-счётчики позиций (is_*).
-- Воронка в BI: Σ original → Σ final → Σ added → Σ shipped, срез по client/niche/n3/n4/месяц.
CREATE MATERIALIZED VIEW IF NOT EXISTS effect_funnel AS
    WITH bounds AS (
        SELECT order_id, min(version_seq) AS first_seq, max(version_seq) AS last_seq
        FROM order_line_versions GROUP BY order_id
    ),
    o AS (   -- исходная версия (O)
        SELECT v.order_id, v.n3_id, v.n4_id, sum(v.qty) AS qty
        FROM order_line_versions v JOIN bounds b
          ON b.order_id = v.order_id AND v.version_seq = b.first_seq
        GROUP BY v.order_id, v.n3_id, v.n4_id
    ),
    f AS (   -- финальная версия (F)
        SELECT v.order_id, v.n3_id, v.n4_id, sum(v.qty) AS qty
        FROM order_line_versions v JOIN bounds b
          ON b.order_id = v.order_id AND v.version_seq = b.last_seq
        GROUP BY v.order_id, v.n3_id, v.n4_id
    ),
    s AS (   -- отгружено по заказу+позиции (m2m по order_id)
        SELECT order_id, n3_id, n4_id, sum(qty) AS qty
        FROM shipment_lines GROUP BY order_id, n3_id, n4_id
    ),
    keys AS (
        SELECT order_id, n3_id, n4_id FROM o
        UNION SELECT order_id, n3_id, n4_id FROM f
        UNION SELECT order_id, n3_id, n4_id FROM s
    ),
    dim AS (  -- измерения на заказ (из версий ∪ отгрузок)
        SELECT order_id, max(client_id) AS client_id, max(niche) AS niche,
               max(order_date) AS order_date
        FROM (
            SELECT order_id, client_id, niche, order_date FROM order_line_versions
            UNION ALL
            SELECT order_id, client_id, niche, shipment_date FROM shipment_lines
        ) z GROUP BY order_id
    )
    SELECT k.order_id, k.n3_id, k.n4_id,
           d.client_id, d.niche,
           date_trunc('month', d.order_date)::date        AS period_month,
           round(COALESCE(o.qty, 0), 3)                    AS qty_original,
           round(COALESCE(f.qty, 0), 3)                    AS qty_final,
           round(GREATEST(COALESCE(f.qty,0) - COALESCE(o.qty,0), 0), 3) AS qty_added,
           round(COALESCE(s.qty, 0), 3)                    AS qty_shipped,
           (COALESCE(o.qty,0) > 0)::int                    AS is_original,
           (COALESCE(f.qty,0) > 0)::int                    AS is_final,
           (COALESCE(o.qty,0) = 0 AND COALESCE(f.qty,0) > 0)::int AS is_added,
           (COALESCE(s.qty,0) > 0)::int                    AS is_shipped
    FROM keys k
    LEFT JOIN o ON o.order_id=k.order_id AND o.n3_id=k.n3_id AND o.n4_id=k.n4_id
    LEFT JOIN f ON f.order_id=k.order_id AND f.n3_id=k.n3_id AND f.n4_id=k.n4_id
    LEFT JOIN s ON s.order_id=k.order_id AND s.n3_id=k.n3_id AND s.n4_id=k.n4_id
    LEFT JOIN dim d ON d.order_id=k.order_id
    WITH NO DATA;
CREATE UNIQUE INDEX IF NOT EXISTS ux_effect_funnel ON effect_funnel (order_id, n3_id, n4_id);
CREATE INDEX IF NOT EXISTS ix_effect_funnel_slice ON effect_funnel (niche, period_month);
