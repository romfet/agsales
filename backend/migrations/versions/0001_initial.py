"""initial schema: order_lines, stock, sync_state + materialized aggregates

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-19
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # --- raw synced rows: one per order line ---
    op.create_table(
        "order_lines",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("order_guid", sa.Text, nullable=False),
        sa.Column("order_num", sa.Text, nullable=False),
        sa.Column("order_date", sa.Date),
        sa.Column("client_guid", sa.Text, nullable=False),
        sa.Column("client_name", sa.Text, nullable=False),
        sa.Column("niche", sa.Text),
        sa.Column("n1", sa.Text),
        sa.Column("n2", sa.Text),
        sa.Column("n3", sa.Text, nullable=False),
        sa.Column("n4", sa.Text, nullable=False),
        sa.Column("item_guid", sa.Text, nullable=False),
        sa.Column("qty", sa.Numeric, nullable=False),
    )
    op.create_index("ix_order_lines_client_guid", "order_lines", ["client_guid"])
    op.create_index("ix_order_lines_niche", "order_lines", ["niche"])
    op.create_index("ix_order_lines_order_guid", "order_lines", ["order_guid"])
    op.execute(
        "CREATE INDEX ix_order_lines_order_num_trgm "
        "ON order_lines USING gin (order_num gin_trgm_ops)"
    )

    op.create_table(
        "stock",
        sa.Column("item_guid", sa.Text, primary_key=True),
        sa.Column("n4_name", sa.Text),
        sa.Column("on_stock", sa.Numeric),
        sa.Column("in_transit", sa.Numeric),
    )

    op.create_table(
        "sync_state",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("status", sa.Text, nullable=False, server_default="empty"),
        sa.Column("last_sync_at", sa.DateTime(timezone=True)),
        sa.Column("row_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error", sa.Text),
    )
    op.execute("INSERT INTO sync_state (id, status) VALUES (1, 'empty')")

    # --- materialized aggregates (created empty; populated by refresh on sync) ---
    op.execute(
        """
        CREATE MATERIALIZED VIEW client_dim AS
        SELECT client_guid,
               max(client_name)                         AS client_name,
               mode() WITHIN GROUP (ORDER BY niche)     AS niche,
               count(DISTINCT order_guid)               AS total_orders
        FROM order_lines
        GROUP BY client_guid
        WITH NO DATA
        """
    )
    op.execute("CREATE UNIQUE INDEX ux_client_dim ON client_dim (client_guid)")

    op.execute(
        """
        CREATE MATERIALIZED VIEW niche_dim AS
        SELECT niche,
               count(DISTINCT client_guid)  AS total_clients_in_niche,
               count(DISTINCT order_guid)   AS total_orders_in_niche
        FROM order_lines
        WHERE niche IS NOT NULL
        GROUP BY niche
        WITH NO DATA
        """
    )
    op.execute("CREATE UNIQUE INDEX ux_niche_dim ON niche_dim (niche)")

    op.execute(
        """
        CREATE MATERIALIZED VIEW products AS
        SELECT DISTINCT n3, n4, item_guid
        FROM order_lines
        WITH NO DATA
        """
    )
    op.execute("CREATE UNIQUE INDEX ux_products ON products (n3, item_guid)")

    op.execute(
        """
        CREATE MATERIALIZED VIEW client_profile_n4 AS
        WITH per AS (
            SELECT client_guid, n3, n4, item_guid,
                   count(DISTINCT order_guid) AS order_count,
                   sum(qty)                   AS total_qty
            FROM order_lines
            GROUP BY client_guid, n3, n4, item_guid
        )
        SELECT p.client_guid, p.n3, p.n4, p.item_guid,
               p.order_count,
               p.total_qty,
               d.total_orders,
               round(p.order_count::numeric / NULLIF(d.total_orders, 0) * 100, 1) AS frequency_pct,
               round(p.total_qty / NULLIF(p.order_count, 0), 3)                   AS avg_qty_per_order
        FROM per p
        JOIN client_dim d USING (client_guid)
        WITH NO DATA
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX ux_client_profile_n4 "
        "ON client_profile_n4 (client_guid, n3, item_guid)"
    )
    op.create_index("ix_client_profile_n4_client", "client_profile_n4", ["client_guid"])

    op.execute(
        """
        CREATE MATERIALIZED VIEW niche_profile_n4 AS
        WITH per AS (
            SELECT niche, n3, n4, item_guid,
                   count(DISTINCT client_guid) AS client_count,
                   count(DISTINCT order_guid)  AS order_count,
                   sum(qty)                    AS total_qty
            FROM order_lines
            WHERE niche IS NOT NULL
            GROUP BY niche, n3, n4, item_guid
        )
        SELECT p.niche, p.n3, p.n4, p.item_guid,
               p.client_count,
               p.order_count,
               p.total_qty,
               nd.total_clients_in_niche,
               nd.total_orders_in_niche,
               round(p.client_count::numeric / NULLIF(nd.total_clients_in_niche, 0) * 100, 1) AS niche_pct,
               round(p.order_count::numeric / NULLIF(nd.total_orders_in_niche, 0) * 100, 1)   AS niche_freq_pct,
               round(p.total_qty / NULLIF(p.client_count, 0), 3)                              AS avg_qty_per_client
        FROM per p
        JOIN niche_dim nd USING (niche)
        WITH NO DATA
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX ux_niche_profile_n4 "
        "ON niche_profile_n4 (niche, n3, item_guid)"
    )
    op.create_index("ix_niche_profile_n4_niche", "niche_profile_n4", ["niche"])


def downgrade() -> None:
    for mv in (
        "niche_profile_n4",
        "client_profile_n4",
        "products",
        "niche_dim",
        "client_dim",
    ):
        op.execute(f"DROP MATERIALIZED VIEW IF EXISTS {mv}")
    op.drop_table("sync_state")
    op.drop_table("stock")
    op.drop_table("order_lines")
