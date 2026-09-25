-- Recommendation profiles at N4 (SKU) grain.
-- Existing N3 views stay in place for backward compatibility with the effect API.
-- Base tables and the 1C ingest contract are unchanged.

CREATE MATERIALIZED VIEW IF NOT EXISTS client_profile_n4 AS
    SELECT s.client_id,
           max(s.n3_id) AS n3_id,
           s.n4_id,
           count(DISTINCT s.order_id) AS order_count,
           count(DISTINCT s.shipment_id) AS shipment_count,
           sum(s.qty) AS total_qty,
           COALESCE(sum(s.qty) FILTER (
               WHERE s.shipment_date >= CURRENT_DATE - INTERVAL '1 year'), 0) AS total_qty_12m,
           d.total_orders,
           d.total_shipments,
           round(count(DISTINCT s.order_id)::numeric
                 / NULLIF(d.total_orders, 0) * 100, 1) AS frequency_pct,
           round(sum(s.qty) / NULLIF(count(DISTINCT s.order_id), 0), 3) AS avg_qty_per_order
    FROM shipment_lines s
    JOIN client_dim d USING (client_id)
    GROUP BY s.client_id, s.n4_id, d.total_orders, d.total_shipments
    WITH NO DATA;
CREATE UNIQUE INDEX IF NOT EXISTS ux_client_profile_n4
    ON client_profile_n4 (client_id, n4_id);
CREATE INDEX IF NOT EXISTS ix_client_profile_n4_client
    ON client_profile_n4 (client_id);

CREATE MATERIALIZED VIEW IF NOT EXISTS niche_profile_n4 AS
    SELECT s.niche,
           max(s.n3_id) AS n3_id,
           s.n4_id,
           count(DISTINCT s.client_id) AS client_count,
           count(DISTINCT s.order_id) AS order_count,
           count(DISTINCT s.shipment_id) AS shipment_count,
           sum(s.qty) AS total_qty,
           COALESCE(sum(s.qty) FILTER (
               WHERE s.shipment_date >= CURRENT_DATE - INTERVAL '1 year'), 0) AS total_qty_12m,
           d.total_clients_in_niche,
           d.total_orders_in_niche,
           d.total_shipments_in_niche,
           round(count(DISTINCT s.client_id)::numeric
                 / NULLIF(d.total_clients_in_niche, 0) * 100, 1) AS niche_pct,
           round(count(DISTINCT s.order_id)::numeric
                 / NULLIF(d.total_orders_in_niche, 0) * 100, 1) AS niche_freq_pct,
           round(sum(s.qty) / NULLIF(count(DISTINCT s.client_id), 0), 3) AS avg_qty_per_client
    FROM shipment_lines s
    JOIN niche_dim d USING (niche)
    WHERE s.niche IS NOT NULL
    GROUP BY s.niche, s.n4_id,
             d.total_clients_in_niche, d.total_orders_in_niche, d.total_shipments_in_niche
    WITH NO DATA;
CREATE UNIQUE INDEX IF NOT EXISTS ux_niche_profile_n4
    ON niche_profile_n4 (niche, n4_id);
CREATE INDEX IF NOT EXISTS ix_niche_profile_n4_niche
    ON niche_profile_n4 (niche);
