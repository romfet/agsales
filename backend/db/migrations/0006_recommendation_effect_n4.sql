-- Direct recommendation effect at N4 grain.
-- Scenario 1: the recommended missing N4 is added to the analyzed order.
-- Scenario 2: the N4 appears in the first eligible new order of the same client
-- within two business days (Mon-Fri; public holidays are not modelled).

CREATE MATERIALIZED VIEW IF NOT EXISTS recommendation_effect_n4 AS
WITH recommendations AS (
    -- Repeated analysis calls must not multiply the effect of one recommendation.
    SELECT DISTINCT ON (client_id, order_id, n4_id)
           id AS recommendation_id,
           created_at AS recommendation_at,
           client_id,
           order_id AS source_order_id,
           kind,
           n3_id,
           n4_id
    FROM recommendation_log
    WHERE order_id IS NOT NULL AND n4_id IS NOT NULL
    ORDER BY client_id, order_id, n4_id, created_at, id
),
bounds AS (
    SELECT order_id, min(version_seq) AS first_seq, max(version_seq) AS last_seq
    FROM order_line_versions
    GROUP BY order_id
),
first_lines AS (
    SELECT v.order_id, v.n3_id, v.n4_id, sum(v.qty) AS qty
    FROM order_line_versions v
    JOIN bounds b ON b.order_id = v.order_id AND b.first_seq = v.version_seq
    GROUP BY v.order_id, v.n3_id, v.n4_id
),
last_lines AS (
    SELECT v.order_id, v.n3_id, v.n4_id, sum(v.qty) AS qty
    FROM order_line_versions v
    JOIN bounds b ON b.order_id = v.order_id AND b.last_seq = v.version_seq
    GROUP BY v.order_id, v.n3_id, v.n4_id
),
shipped AS (
    SELECT order_id, n3_id, n4_id, sum(qty) AS qty
    FROM shipment_lines
    GROUP BY order_id, n3_id, n4_id
),
orders AS (
    SELECT order_id,
           max(client_id) AS client_id,
           max(niche) AS niche,
           COALESCE(max(order_date), min(ingested_at)::date) AS order_date,
           min(ingested_at) AS first_seen_at
    FROM order_line_versions
    GROUP BY order_id
),
same_order AS (
    SELECT r.recommendation_id,
           r.recommendation_at,
           r.client_id,
           o.niche,
           r.kind,
           'same_order'::text AS scenario,
           r.source_order_id,
           r.source_order_id AS target_order_id,
           r.n3_id,
           r.n4_id,
           o.order_date AS target_order_date,
           round(f.qty, 3) AS ordered_qty,
           round(s.qty, 3) AS shipped_qty,
           round(LEAST(f.qty, s.qty), 3) AS realized_qty
    FROM recommendations r
    JOIN orders o ON o.order_id = r.source_order_id
    JOIN last_lines f
      ON f.order_id = r.source_order_id AND f.n4_id = r.n4_id
    JOIN shipped s
      ON s.order_id = r.source_order_id AND s.n4_id = r.n4_id
    LEFT JOIN first_lines original
      ON original.order_id = r.source_order_id AND original.n4_id = r.n4_id
    WHERE COALESCE(original.qty, 0) = 0 AND f.qty > 0 AND s.qty > 0
),
new_candidates AS (
    SELECT r.recommendation_id,
           r.recommendation_at,
           r.client_id,
           target.niche,
           r.kind,
           'new_order'::text AS scenario,
           r.source_order_id,
           target.order_id AS target_order_id,
           f.n3_id,
           r.n4_id,
           target.order_date AS target_order_date,
           round(f.qty, 3) AS ordered_qty,
           round(s.qty, 3) AS shipped_qty,
           round(LEAST(f.qty, s.qty), 3) AS realized_qty,
           row_number() OVER (
               PARTITION BY target.order_id, r.n4_id
               ORDER BY r.recommendation_at DESC, r.recommendation_id DESC
           ) AS target_rank
    FROM recommendations r
    JOIN orders target
      ON target.client_id = r.client_id AND target.order_id <> r.source_order_id
    JOIN last_lines f
      ON f.order_id = target.order_id AND f.n4_id = r.n4_id
    JOIN shipped s
      ON s.order_id = target.order_id AND s.n4_id = r.n4_id
    LEFT JOIN first_lines source_original
      ON source_original.order_id = r.source_order_id AND source_original.n4_id = r.n4_id
    WHERE COALESCE(source_original.qty, 0) = 0
      AND f.qty > 0
      AND s.qty > 0
      AND target.first_seen_at >= r.recommendation_at
      AND target.order_date >= r.recommendation_at::date
      AND target.order_date <= r.recommendation_at::date +
          CASE EXTRACT(ISODOW FROM r.recommendation_at)
              WHEN 4 THEN 4
              WHEN 5 THEN 4
              WHEN 6 THEN 3
              WHEN 7 THEN 2
              ELSE 2
          END
),
new_one_per_recommendation AS (
    SELECT c.*,
           row_number() OVER (
               PARTITION BY c.recommendation_id, c.n4_id
               ORDER BY c.target_order_date, c.target_order_id
           ) AS recommendation_rank
    FROM new_candidates c
    WHERE c.target_rank = 1
)
SELECT recommendation_id, recommendation_at, client_id, niche, kind, scenario,
       source_order_id, target_order_id, n3_id, n4_id, target_order_date,
       ordered_qty, shipped_qty, realized_qty
FROM same_order
UNION ALL
SELECT recommendation_id, recommendation_at, client_id, niche, kind, scenario,
       source_order_id, target_order_id, n3_id, n4_id, target_order_date,
       ordered_qty, shipped_qty, realized_qty
FROM new_one_per_recommendation
WHERE recommendation_rank = 1
WITH NO DATA;

CREATE UNIQUE INDEX IF NOT EXISTS ux_recommendation_effect_n4
    ON recommendation_effect_n4 (scenario, source_order_id, target_order_id, n4_id);
CREATE INDEX IF NOT EXISTS ix_recommendation_effect_n4_slice
    ON recommendation_effect_n4 (niche, target_order_date);
