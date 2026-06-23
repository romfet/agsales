"""switch identity to GUID keys: client_id / n3_id / n4_id

Revision ID: 0004_guid_keys
Revises: 0003_drop_staging_stock
Create Date: 2026-06-23

Rebuilds order_lines + all materialized views around GUID identity (client_id,
n3_id, n4_id); names (client_name/n3_name/n4_name) are display-only; order key is
order_num. Destructive (drops + recreates) — fine on the test stand (mock data is
re-seeded). Downgrade restores the previous name-based schema (0001-style).
"""
from alembic import op

revision = "0004_guid_keys"
down_revision = "0003_drop_staging_stock"
branch_labels = None
depends_on = None

_NEW_MVS = ["niche_profile_n4", "client_profile_n4", "products", "niche_dim", "client_dim"]


def _drop_all():
    for mv in _NEW_MVS:
        op.execute(f"DROP MATERIALIZED VIEW IF EXISTS {mv}")
    op.execute("DROP TABLE IF EXISTS order_lines")


def upgrade() -> None:
    _drop_all()

    op.execute(
        """
        CREATE TABLE order_lines (
            id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            order_num   text NOT NULL,
            order_date  date,
            client_id   text NOT NULL,
            client_name text,
            niche       text,
            n3_id       text NOT NULL,
            n3_name     text,
            n4_id       text NOT NULL,
            n4_name     text,
            qty         numeric NOT NULL
        )
        """
    )
    op.create_index("ix_order_lines_order_num", "order_lines", ["order_num"])
    op.create_index("ix_order_lines_client_id", "order_lines", ["client_id"])
    op.create_index("ix_order_lines_niche", "order_lines", ["niche"])

    op.execute(
        """
        CREATE MATERIALIZED VIEW client_dim AS
        SELECT client_id,
               max(client_name)                      AS client_name,
               mode() WITHIN GROUP (ORDER BY niche)  AS niche,
               count(DISTINCT order_num)             AS total_orders
        FROM order_lines GROUP BY client_id WITH NO DATA
        """
    )
    op.execute("CREATE UNIQUE INDEX ux_client_dim ON client_dim (client_id)")

    op.execute(
        """
        CREATE MATERIALIZED VIEW niche_dim AS
        SELECT niche,
               count(DISTINCT client_id)  AS total_clients_in_niche,
               count(DISTINCT order_num)  AS total_orders_in_niche
        FROM order_lines WHERE niche IS NOT NULL GROUP BY niche WITH NO DATA
        """
    )
    op.execute("CREATE UNIQUE INDEX ux_niche_dim ON niche_dim (niche)")

    op.execute(
        """
        CREATE MATERIALIZED VIEW products AS
        SELECT n4_id,
               max(n4_name) AS n4_name,
               max(n3_id)   AS n3_id,
               max(n3_name) AS n3_name
        FROM order_lines GROUP BY n4_id WITH NO DATA
        """
    )
    op.execute("CREATE UNIQUE INDEX ux_products ON products (n4_id)")

    op.execute(
        """
        CREATE MATERIALIZED VIEW client_profile_n4 AS
        WITH per AS (
            SELECT client_id, n4_id,
                   max(n3_id) AS n3_id, max(n3_name) AS n3_name, max(n4_name) AS n4_name,
                   count(DISTINCT order_num) AS order_count,
                   sum(qty)                  AS total_qty
            FROM order_lines GROUP BY client_id, n4_id
        )
        SELECT p.client_id, p.n3_id, p.n3_name, p.n4_id, p.n4_name,
               p.order_count, p.total_qty, d.total_orders,
               round(p.order_count::numeric / NULLIF(d.total_orders, 0) * 100, 1) AS frequency_pct,
               round(p.total_qty / NULLIF(p.order_count, 0), 3)                   AS avg_qty_per_order
        FROM per p JOIN client_dim d USING (client_id)
        WITH NO DATA
        """
    )
    op.execute("CREATE UNIQUE INDEX ux_client_profile_n4 ON client_profile_n4 (client_id, n4_id)")
    op.create_index("ix_client_profile_n4_client", "client_profile_n4", ["client_id"])

    op.execute(
        """
        CREATE MATERIALIZED VIEW niche_profile_n4 AS
        WITH per AS (
            SELECT niche, n4_id,
                   max(n3_id) AS n3_id, max(n3_name) AS n3_name, max(n4_name) AS n4_name,
                   count(DISTINCT client_id) AS client_count,
                   count(DISTINCT order_num) AS order_count,
                   sum(qty)                  AS total_qty
            FROM order_lines WHERE niche IS NOT NULL GROUP BY niche, n4_id
        )
        SELECT p.niche, p.n3_id, p.n3_name, p.n4_id, p.n4_name,
               p.client_count, p.order_count, p.total_qty,
               nd.total_clients_in_niche, nd.total_orders_in_niche,
               round(p.client_count::numeric / NULLIF(nd.total_clients_in_niche, 0) * 100, 1) AS niche_pct,
               round(p.order_count::numeric  / NULLIF(nd.total_orders_in_niche, 0) * 100, 1) AS niche_freq_pct,
               round(p.total_qty / NULLIF(p.client_count, 0), 3)                              AS avg_qty_per_client
        FROM per p JOIN niche_dim nd USING (niche)
        WITH NO DATA
        """
    )
    op.execute("CREATE UNIQUE INDEX ux_niche_profile_n4 ON niche_profile_n4 (niche, n4_id)")
    op.create_index("ix_niche_profile_n4_niche", "niche_profile_n4", ["niche"])


def downgrade() -> None:
    _drop_all()
    # restore previous name-based schema (0001-style)
    op.execute(
        """
        CREATE TABLE order_lines (
            id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            order_guid  text NOT NULL, order_num text NOT NULL, order_date date,
            client_guid text NOT NULL, client_name text NOT NULL, niche text,
            n1 text, n2 text, n3 text NOT NULL, n4 text NOT NULL,
            item_guid text NOT NULL, qty numeric NOT NULL
        )
        """
    )
    op.execute("CREATE MATERIALIZED VIEW client_dim AS SELECT client_guid, max(client_name) AS client_name, mode() WITHIN GROUP (ORDER BY niche) AS niche, count(DISTINCT order_guid) AS total_orders FROM order_lines GROUP BY client_guid WITH NO DATA")
    op.execute("CREATE UNIQUE INDEX ux_client_dim ON client_dim (client_guid)")
    op.execute("CREATE MATERIALIZED VIEW niche_dim AS SELECT niche, count(DISTINCT client_guid) AS total_clients_in_niche, count(DISTINCT order_guid) AS total_orders_in_niche FROM order_lines WHERE niche IS NOT NULL GROUP BY niche WITH NO DATA")
    op.execute("CREATE UNIQUE INDEX ux_niche_dim ON niche_dim (niche)")
    op.execute("CREATE MATERIALIZED VIEW products AS SELECT DISTINCT n3, n4, item_guid FROM order_lines WITH NO DATA")
    op.execute("CREATE UNIQUE INDEX ux_products ON products (n3, item_guid)")
    op.execute("CREATE MATERIALIZED VIEW client_profile_n4 AS WITH per AS (SELECT client_guid, n3, n4, item_guid, count(DISTINCT order_guid) AS order_count, sum(qty) AS total_qty FROM order_lines GROUP BY client_guid, n3, n4, item_guid) SELECT p.client_guid, p.n3, p.n4, p.item_guid, p.order_count, p.total_qty, d.total_orders, round(p.order_count::numeric/NULLIF(d.total_orders,0)*100,1) AS frequency_pct, round(p.total_qty/NULLIF(p.order_count,0),3) AS avg_qty_per_order FROM per p JOIN client_dim d USING (client_guid) WITH NO DATA")
    op.execute("CREATE UNIQUE INDEX ux_client_profile_n4 ON client_profile_n4 (client_guid, n3, item_guid)")
    op.execute("CREATE MATERIALIZED VIEW niche_profile_n4 AS WITH per AS (SELECT niche, n3, n4, item_guid, count(DISTINCT client_guid) AS client_count, count(DISTINCT order_guid) AS order_count, sum(qty) AS total_qty FROM order_lines WHERE niche IS NOT NULL GROUP BY niche, n3, n4, item_guid) SELECT p.niche, p.n3, p.n4, p.item_guid, p.client_count, p.order_count, p.total_qty, nd.total_clients_in_niche, nd.total_orders_in_niche, round(p.client_count::numeric/NULLIF(nd.total_clients_in_niche,0)*100,1) AS niche_pct, round(p.order_count::numeric/NULLIF(nd.total_orders_in_niche,0)*100,1) AS niche_freq_pct, round(p.total_qty/NULLIF(p.client_count,0),3) AS avg_qty_per_client FROM per p JOIN niche_dim nd USING (niche) WITH NO DATA")
    op.execute("CREATE UNIQUE INDEX ux_niche_profile_n4 ON niche_profile_n4 (niche, n3, item_guid)")
