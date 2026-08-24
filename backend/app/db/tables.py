"""Table/materialized-view definitions used for *reading* and ingest.

Schema is created by backend/db/schema.sql (no migrations). These Core objects
exist only so the repository can build typed, parameterized queries.

Identity by GUID: client → client_id, subgroup → n3_id, product → n4_id.
Names of subgroup/product are NOT stored (1С resolves them by GUID).
Order key = order_num; niche = Отрасль.
"""
from sqlalchemy import (
    BigInteger,
    Boolean,
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
    # Маркер рекомендации (проставляет 1С; см. schema.sql).
    Column("rec_source", Boolean),
    Column("rec_kind", Text),
    Column("rec_origin_order", Text),
)

# Append-only снимки версий заказа (см. schema.sql). id/ingested_at заполняются
# БД по умолчанию — при вставке передаём те же поля, что и в order_lines.
order_line_versions = Table(
    "order_line_versions",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("ingested_at", DateTime(timezone=True)),
    Column("order_id", Text, nullable=False),
    Column("order_num", Text, nullable=False),
    Column("order_date", Date),
    Column("client_id", Text, nullable=False),
    Column("client_name", Text),
    Column("niche", Text),
    Column("n3_id", Text, nullable=False),
    Column("n4_id", Text, nullable=False),
    Column("qty", Numeric, nullable=False),
    Column("rec_source", Boolean),
    Column("rec_kind", Text),
    Column("rec_origin_order", Text),
)

client_dim = Table(
    "client_dim",
    metadata,
    Column("client_id", Text, primary_key=True),
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
    Column("n4_id", Text),
    Column("n3_id", Text),
)

client_profile_n4 = Table(
    "client_profile_n4",
    metadata,
    Column("client_id", Text),
    Column("n3_id", Text),
    Column("n4_id", Text),
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
    Column("n3_id", Text),
    Column("n4_id", Text),
    Column("client_count", Integer),
    Column("order_count", Integer),
    Column("total_qty", Numeric),
    Column("total_clients_in_niche", Integer),
    Column("total_orders_in_niche", Integer),
    Column("niche_pct", Numeric),
    Column("niche_freq_pct", Numeric),
    Column("avg_qty_per_client", Numeric),
)

client_profile_n3 = Table(
    "client_profile_n3",
    metadata,
    Column("client_id", Text),
    Column("n3_id", Text),
    Column("rep_n4_id", Text),
    Column("order_count", Integer),
    Column("total_qty", Numeric),
    Column("total_orders", Integer),
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
    "client_profile_n3",
    "niche_profile_n3",
]
