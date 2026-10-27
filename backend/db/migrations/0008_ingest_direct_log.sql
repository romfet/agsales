-- Миграция 0008 — статистика приёма в режиме direct (видимость «документ пришёл частями»).
--
-- В режиме INGEST_MODE=direct каждый запрос заменяет свои документы целиком. Если 1С снова
-- начнёт передавать документ частями, документы молча сузятся до последней части. Каждая
-- пачка пишет сюда одну строку итогов; GET /api/ingest/stats показывает сумму за сутки.
-- Старые строки чистит worker (INGEST_BATCH_RETENTION_DAYS). Аддитивно.

CREATE TABLE IF NOT EXISTS ingest_direct_log (
    id                  bigserial PRIMARY KEY,
    received_at         timestamptz NOT NULL DEFAULT now(),
    kind                text NOT NULL CHECK (kind IN ('orders', 'shipments')),
    items               integer NOT NULL,
    documents           integer NOT NULL,
    documents_shrunk    integer NOT NULL,   -- в БД были товары, которых нет в пачке
    single_product_docs integer NOT NULL,   -- документов с одним товаром в пачке
    shrunk_sample       text                -- пример сузившегося документа (для разбора)
);
CREATE INDEX IF NOT EXISTS ix_ingest_direct_log_at ON ingest_direct_log (received_at);
