-- Миграция 0007 — сборка документов из пачек при приёме (SDD_сборка_документов_при_приёме.md).
--
-- Зачем: 1С передаёт строки одного заказа / РТУ в разных запросах (фактически по товару),
-- а приём трактовал каждый запрос как полный состав документа → в БД оставался последний
-- присланный товар. В режиме INGEST_MODE=staged пачки складываются в staging и собираются
-- worker'ом: по каждому товару документа берутся строки из последней пачки с этим товаром.
--
-- Семантика order_line_versions.version_seq: в режиме direct — версия на пачку (как в 0001),
-- в режиме staged — одна версия на сборку. merge_id связывает версию со сборкой.
-- Все объекты аддитивны: боевые таблицы и матвью не меняются (кроме ADD COLUMN merge_id).

CREATE SEQUENCE IF NOT EXISTS ingest_batch_seq;

CREATE TABLE IF NOT EXISTS ingest_batches (
    batch_id      bigint PRIMARY KEY DEFAULT nextval('ingest_batch_seq'),
    kind          text NOT NULL CHECK (kind IN ('orders', 'shipments')),
    received_at   timestamptz NOT NULL DEFAULT now(),
    payload_hash  text NOT NULL,
    items         integer NOT NULL,
    merged_at     timestamptz,
    merge_id      bigint
);
CREATE INDEX IF NOT EXISTS ix_ingest_batches_pending
    ON ingest_batches (kind, batch_id) WHERE merged_at IS NULL;
-- Повтор несобранной пачки (ретрай 1С) не пишется второй раз.
CREATE UNIQUE INDEX IF NOT EXISTS ux_ingest_batches_pending_hash
    ON ingest_batches (kind, payload_hash) WHERE merged_at IS NULL;

CREATE TABLE IF NOT EXISTS staging_order_lines (
    batch_id    bigint NOT NULL,
    order_id    text NOT NULL,
    order_num   text NOT NULL,
    order_date  date,
    client_id   text NOT NULL,
    client_name text,
    niche       text,
    n3_id       text NOT NULL,
    n4_id       text NOT NULL,
    qty         numeric NOT NULL,
    deleted     boolean NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS ix_staging_order_lines_doc ON staging_order_lines (order_id, batch_id);
CREATE INDEX IF NOT EXISTS ix_staging_order_lines_batch ON staging_order_lines (batch_id);

CREATE TABLE IF NOT EXISTS staging_shipment_lines (
    batch_id      bigint NOT NULL,
    shipment_id   text NOT NULL,
    shipment_num  text,
    shipment_date date,
    order_id      text NOT NULL,
    client_id     text NOT NULL,
    client_name   text,
    niche         text,
    n3_id         text NOT NULL,
    n4_id         text NOT NULL,
    qty           numeric NOT NULL,
    deleted       boolean NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS ix_staging_shipment_lines_doc ON staging_shipment_lines (shipment_id, batch_id);
CREATE INDEX IF NOT EXISTS ix_staging_shipment_lines_batch ON staging_shipment_lines (batch_id);

-- Последняя применённая пачка по товару документа: поздно зафиксированная старая пачка
-- не перекрывает более новую.
CREATE TABLE IF NOT EXISTS staging_applied (
    kind     text NOT NULL,
    doc_id   text NOT NULL,
    n4_id    text NOT NULL,
    batch_id bigint NOT NULL,
    PRIMARY KEY (kind, doc_id, n4_id)
);

CREATE TABLE IF NOT EXISTS ingest_merges (
    merge_id                bigserial PRIMARY KEY,
    kind                    text NOT NULL,
    trigger                 text NOT NULL,      -- commit | idle | max_delay | manual | recovery
    requested_at            timestamptz NOT NULL DEFAULT now(),
    started_at              timestamptz,
    finished_at             timestamptz,
    shrink_mode             text,
    batches                 integer,
    documents               integer,
    lines                   integer,
    documents_shrunk        integer,
    documents_qty_decreased integer,
    positions_kept          integer,
    single_product_share    numeric,
    error                   text
);
CREATE INDEX IF NOT EXISTS ix_ingest_merges_pending
    ON ingest_merges (kind, merge_id) WHERE started_at IS NULL;

ALTER TABLE order_line_versions ADD COLUMN IF NOT EXISTS merge_id bigint;
