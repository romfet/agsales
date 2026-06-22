"""drop vestigial staging + stock tables

Revision ID: 0003_drop_staging_stock
Revises: 0002_staging
Create Date: 2026-06-22

The pull pipeline (staging -> swap) was replaced by push ingest, and stock was
dropped from the integration entirely. So order_lines_staging, stock_staging and
stock are now unused — remove them. Downgrade recreates them as they were
(stock per 0001, staging per 0002).
"""
from alembic import op

revision = "0003_drop_staging_stock"
down_revision = "0002_staging"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS order_lines_staging")
    op.execute("DROP TABLE IF EXISTS stock_staging")
    op.execute("DROP TABLE IF EXISTS stock")


def downgrade() -> None:
    op.execute(
        """
        CREATE TABLE stock (
            item_guid   text PRIMARY KEY,
            n4_name     text,
            on_stock    numeric,
            in_transit  numeric
        )
        """
    )
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
