"""Table/materialized-view definitions used for *reading* and ingest.

Schema is created by the migrations in backend/db/migrations/ (see db/migrate.py).
These Core objects exist only so the repository can build typed, parameterized queries.

Identity by GUID: client → client_id, subgroup → n3_id, product → n4_id.
Names of subgroup/product are NOT stored (1С resolves them by GUID).
Order key = order_id (GUID); order_num is a display number. niche = Отрасль (GUID).
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

order_lines = Table(
    "order_lines",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("order_id", Text, nullable=False),
    Column("order_num", Text, nullable=False),
    Column("order_date", Date),
    Column("client_id", Text, nullable=False),
    Column("client_name", Text),
    Column("niche", Text),
    Column("n3_id", Text, nullable=False),
    Column("n4_id", Text, nullable=False),
    Column("qty", Numeric, nullable=False),
)

# Append-only снимки версий заказа (см. migrations/0001_baseline.sql). id/ingested_at/version_seq
# заполняются при вставке.
order_line_versions = Table(
    "order_line_versions",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("version_seq", BigInteger),
    Column("ingested_at", DateTime(timezone=True)),
    Column("content_hash", Text),
    Column("order_id", Text, nullable=False),
    Column("order_num", Text, nullable=False),
    Column("order_date", Date),
    Column("client_id", Text, nullable=False),
    Column("client_name", Text),
    Column("niche", Text),
    Column("n3_id", Text, nullable=False),
    Column("n4_id", Text, nullable=False),
    Column("qty", Numeric, nullable=False),
)

shipment_lines = Table(
    "shipment_lines",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("shipment_id", Text, nullable=False),
    Column("shipment_num", Text),
    Column("shipment_date", Date),
    Column("order_id", Text, nullable=False),
    Column("client_id", Text, nullable=False),
    Column("client_name", Text),
    Column("niche", Text),
    Column("n3_id", Text, nullable=False),
    Column("n4_id", Text, nullable=False),
    Column("qty", Numeric, nullable=False),
)

recommendation_log = Table(
    "recommendation_log",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("created_at", DateTime(timezone=True)),
    Column("client_id", Text, nullable=False),
    Column("order_id", Text),
    Column("kind", Text, nullable=False),
    Column("n3_id", Text, nullable=False),
    Column("n4_id", Text),
    Column("priority", Text),
)

client_dim = Table(
    "client_dim",
    metadata,
    Column("client_id", Text, primary_key=True),
    Column("client_name", Text),
    Column("niche", Text),
    Column("total_orders", Integer),
    Column("total_shipments", Integer),
)

niche_dim = Table(
    "niche_dim",
    metadata,
    Column("niche", Text, primary_key=True),
    Column("total_clients_in_niche", Integer),
    Column("total_orders_in_niche", Integer),
    Column("total_shipments_in_niche", Integer),
)

products = Table(
    "products",
    metadata,
    Column("n4_id", Text),
    Column("n3_id", Text),
)

client_profile_n3 = Table(
    "client_profile_n3",
    metadata,
    Column("client_id", Text),
    Column("n3_id", Text),
    Column("rep_n4_id", Text),
    Column("order_count", Integer),
    Column("shipment_count", Integer),
    Column("total_qty", Numeric),
    Column("total_qty_12m", Numeric),
    Column("total_orders", Integer),
    Column("total_shipments", Integer),
    Column("frequency_pct", Numeric),
    Column("avg_qty_per_order", Numeric),
)

niche_profile_n3 = Table(
    "niche_profile_n3",
    metadata,
    Column("niche", Text),
    Column("n3_id", Text),
    Column("rep_n4_id", Text),
    Column("client_count", Integer),
    Column("order_count", Integer),
    Column("shipment_count", Integer),
    Column("total_qty", Numeric),
    Column("total_qty_12m", Numeric),
    Column("total_clients_in_niche", Integer),
    Column("total_orders_in_niche", Integer),
    Column("total_shipments_in_niche", Integer),
    Column("niche_pct", Numeric),
    Column("niche_freq_pct", Numeric),
    Column("avg_qty_per_client", Numeric),
)

# Ниша клиента (мода) из заказов ∪ отгрузок — резолв ниши, работает и до отгрузок.
client_niche = Table(
    "client_niche",
    metadata,
    Column("client_id", Text, primary_key=True),
    Column("niche", Text),
)

# Витрина воронки эффекта (v2, грейн order_id × n3_id) — для выгрузки в BI.
effect_funnel = Table(
    "effect_funnel",
    metadata,
    Column("order_id", Text),
    Column("n3_id", Text),
    Column("client_id", Text),
    Column("niche", Text),
    Column("period_month", Date),
    Column("qty_original", Numeric),
    Column("qty_final", Numeric),
    Column("qty_added", Numeric),
    Column("qty_shipped", Numeric),
    Column("is_original", Integer),
    Column("is_final", Integer),
    Column("is_new_position", Integer),
    Column("is_qty_increased", Integer),
    Column("is_shipped", Integer),
)

# Refresh order respects matview dependencies (dims before profiles).
MATERIALIZED_VIEWS = [
    "client_dim",
    "niche_dim",
    "products",
    "client_niche",
    "client_profile_n3",
    "niche_profile_n3",
    "effect_funnel",
]
