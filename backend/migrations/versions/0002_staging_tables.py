"""staging tables for the sync pipeline (staging -> atomic swap)

Revision ID: 0002_staging
Revises: 0001_initial
Create Date: 2026-06-21

UNLOGGED tables: this is transient landing data (truncated + reloaded every
sync, then swapped into the live tables), so skipping WAL is both safe and
faster. They mirror the data columns of order_lines / stock — no indexes needed
(only bulk insert + a single full-scan INSERT...SELECT into the live tables).
"""
from alembic import op

revision = "0002_staging"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE UNLOGGED TABLE order_lines_staging (
            order_guid  text NOT NULL,
            order_num   text NOT NULL,
            order_date  date,
            client_guid text NOT NULL,
            client_name text NOT NULL,
            niche       text,
            n1          text,
            n2          text,
            n3          text NOT NULL,
            n4          text NOT NULL,
            item_guid   text NOT NULL,
            qty         numeric NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE UNLOGGED TABLE stock_staging (
            item_guid   text,
            n4_name     text,
            on_stock    numeric,
            in_transit  numeric
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS stock_staging")
    op.execute("DROP TABLE IF EXISTS order_lines_staging")
