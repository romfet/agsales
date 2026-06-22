"""Table/materialized-view definitions used for *reading* and seeding.

This metadata is intentionally NOT handed to Alembic (the schema is created by a
hand-written migration that includes materialized views, which Alembic cannot
autogenerate). These objects exist only so the repository can build typed,
parameterized Core queries against them.
"""
from sqlalchemy import (
    BigInteger,
    Column,
    Date,
    DateTime,
    Integer,
    MetaData,
    Numeric,
    Table,
    Text,
)

metadata = MetaData()

# --- Synced raw data ---
order_lines = Table(
    "order_lines",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("order_guid", Text, nullable=False),
    Column("order_num", Text, nullable=False),
    Column("order_date", Date),
    Column("client_guid", Text, nullable=False),
    Column("client_name", Text, nullable=False),
    Column("niche", Text),
    Column("n1", Text),
    Column("n2", Text),
    Column("n3", Text, nullable=False),
    Column("n4", Text, nullable=False),
    Column("item_guid", Text, nullable=False),
    Column("qty", Numeric, nullable=False),
)

stock = Table(
    "stock",
    metadata,
    Column("item_guid", Text, primary_key=True),
    Column("n4_name", Text),
    Column("on_stock", Numeric),
    Column("in_transit", Numeric),
)

sync_state = Table(
    "sync_state",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("status", Text, nullable=False),
    Column("last_sync_at", DateTime(timezone=True)),
    Column("row_count", Integer, nullable=False),
    Column("error", Text),
)

# --- Staging tables (transient landing for the sync pipeline; see migration 0002) ---
# Same data columns as order_lines/stock, minus order_lines' Identity `id`. The
# pipeline bulk-loads the full pull here, then swaps it into the live tables.
order_lines_staging = Table(
    "order_lines_staging",
    metadata,
    Column("order_guid", Text, nullable=False),
    Column("order_num", Text, nullable=False),
    Column("order_date", Date),
    Column("client_guid", Text, nullable=False),
    Column("client_name", Text, nullable=False),
    Column("niche", Text),
    Column("n1", Text),
    Column("n2", Text),
    Column("n3", Text, nullable=False),
    Column("n4", Text, nullable=False),
    Column("item_guid", Text, nullable=False),
    Column("qty", Numeric, nullable=False),
)

stock_staging = Table(
    "stock_staging",
    metadata,
    Column("item_guid", Text),
    Column("n4_name", Text),
    Column("on_stock", Numeric),
    Column("in_transit", Numeric),
)

# --- Materialized aggregates (rebuilt on every sync) ---
client_dim = Table(
    "client_dim",
    metadata,
    Column("client_guid", Text, primary_key=True),
    Column("client_name", Text),
    Column("niche", Text),
    Column("total_orders", Integer),
)

niche_dim = Table(
    "niche_dim",
    metadata,
    Column("niche", Text, primary_key=True),
    Column("total_clients_in_niche", Integer),
    Column("total_orders_in_niche", Integer),
)

products = Table(
    "products",
    metadata,
    Column("n3", Text),
    Column("n4", Text),
    Column("item_guid", Text),
)

client_profile_n4 = Table(
    "client_profile_n4",
    metadata,
    Column("client_guid", Text),
    Column("n3", Text),
    Column("n4", Text),
    Column("item_guid", Text),
    Column("order_count", Integer),
    Column("total_qty", Numeric),
    Column("total_orders", Integer),
    Column("frequency_pct", Numeric),
    Column("avg_qty_per_order", Numeric),
)

niche_profile_n4 = Table(
    "niche_profile_n4",
    metadata,
    Column("niche", Text),
    Column("n3", Text),
    Column("n4", Text),
    Column("item_guid", Text),
    Column("client_count", Integer),
    Column("order_count", Integer),
    Column("total_qty", Numeric),
    Column("total_clients_in_niche", Integer),
    Column("total_orders_in_niche", Integer),
    Column("niche_pct", Numeric),
    Column("niche_freq_pct", Numeric),
    Column("avg_qty_per_client", Numeric),
)

# Refresh order respects matview dependencies (dims before profiles).
MATERIALIZED_VIEWS = [
    "client_dim",
    "niche_dim",
    "products",
    "client_profile_n4",
    "niche_profile_n4",
]
