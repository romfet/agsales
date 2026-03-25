"""
Data store module — loads Excel, builds aggregates, holds state in memory.
"""
import re
import pandas as pd


def _natural_sort_key(s: str):
    """Sort strings with embedded numbers naturally: 'Клиент 2' before 'Клиент 10'."""
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r'(\d+)', s)]

# Column name constants
COL_NICHE = "Клиентская ниша"
COL_CLIENT = "Клиент"
COL_ORDER_NUM = "ЗаказНомер"
COL_ORDER_DATE = "ЗаказДата"
COL_N1 = "N1 (товарный раздел)"
COL_N2 = "N2 (товарная группа)"
COL_N3 = "N3 (товарная подгруппа)"
COL_N4 = "N4 (товар)"
COL_QTY = "Количество"

REQUIRED_COLUMNS = [COL_NICHE, COL_CLIENT, COL_ORDER_NUM, COL_ORDER_DATE, COL_N3, COL_N4, COL_QTY]

# Global state
_raw_df: pd.DataFrame | None = None
_client_profile: pd.DataFrame | None = None
_niche_profile: pd.DataFrame | None = None
_clients: list[str] = []
_client_niche: dict[str, str] = {}
_products_n3: list[str] = []
_products_n4_by_n3: dict[str, list[str]] = {}
_total_orders_per_client: dict[str, int] = {}
_upload_info: dict | None = None


def load_excel(filepath: str, column_mapping: dict[str, str] | None = None) -> dict:
    """Parse Excel, build all aggregates. Returns summary stats.

    Args:
        filepath: path to Excel file.
        column_mapping: optional {internal_name: original_col_name} mapping.
            If provided, columns are renamed before validation.
    """
    global _raw_df, _client_profile, _niche_profile
    global _clients, _client_niche, _products_n3, _products_n4_by_n3
    global _total_orders_per_client, _upload_info

    df = pd.read_excel(filepath, sheet_name=0)

    # Apply column mapping if provided
    if column_mapping:
        rename_map = {orig: internal for internal, orig in column_mapping.items() if orig}
        df = df.rename(columns=rename_map)

    # Validate columns
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"В файле отсутствуют колонки: {missing}")

    # Ensure date column is datetime
    if COL_ORDER_DATE in df.columns and not pd.api.types.is_datetime64_any_dtype(df[COL_ORDER_DATE]):
        df[COL_ORDER_DATE] = pd.to_datetime(df[COL_ORDER_DATE], dayfirst=True, errors="coerce")

    # Ensure quantity column is numeric
    if COL_QTY in df.columns and not pd.api.types.is_numeric_dtype(df[COL_QTY]):
        df[COL_QTY] = pd.to_numeric(df[COL_QTY], errors="coerce")

    # Drop rows with NaN in critical columns
    df = df.dropna(subset=[COL_CLIENT, COL_N3, COL_QTY])

    # Optimize memory with categoricals
    for col in [COL_NICHE, COL_CLIENT, COL_N1, COL_N2, COL_N3, COL_N4]:
        if col in df.columns:
            df[col] = df[col].astype("category")

    _raw_df = df

    # --- Build lookups ---
    _clients = list(dict.fromkeys(df[COL_CLIENT].tolist()))
    _products_n3 = sorted(df[COL_N3].cat.categories.tolist())

    # Client -> niche mapping (take the most common niche per client)
    _client_niche = (
        df.groupby(COL_CLIENT, observed=True)[COL_NICHE]
        .agg(lambda x: x.mode().iloc[0])
        .to_dict()
    )

    # N3 -> list of N4
    _products_n4_by_n3 = (
        df.groupby(COL_N3, observed=True)[COL_N4]
        .apply(lambda x: sorted(x.dropna().unique().tolist()))
        .to_dict()
    )

    # Total unique orders per client
    _total_orders_per_client = (
        df.groupby(COL_CLIENT, observed=True)[COL_ORDER_NUM].nunique().to_dict()
    )

    # --- Build client profile ---
    # For each (client, N3): how many orders, total qty, avg qty
    cp = df.groupby([COL_CLIENT, COL_N3], observed=True).agg(
        order_count=(COL_ORDER_NUM, "nunique"),
        total_qty=(COL_QTY, "sum"),
        last_order_date=(COL_ORDER_DATE, "max"),
    ).reset_index()

    # Add total orders for this client
    cp["total_orders"] = cp[COL_CLIENT].map(_total_orders_per_client).astype(int)
    cp["frequency_pct"] = (cp["order_count"] / cp["total_orders"] * 100).round(1)
    cp["avg_qty_per_order"] = (cp["total_qty"] / cp["order_count"]).round(3)

    # Add typical N4 items (top 3 by frequency)
    n4_top = (
        df.groupby([COL_CLIENT, COL_N3, COL_N4], observed=True)
        .agg(n4_count=(COL_ORDER_NUM, "nunique"))
        .reset_index()
        .sort_values("n4_count", ascending=False)
        .groupby([COL_CLIENT, COL_N3], observed=True)[COL_N4]
        .apply(lambda x: x.head(3).tolist())
        .reset_index()
        .rename(columns={COL_N4: "typical_n4"})
    )
    cp = cp.merge(n4_top, on=[COL_CLIENT, COL_N3], how="left")

    _client_profile = cp

    # --- Build niche profile ---
    # For each (niche, N3): how many unique clients buy it
    total_clients_per_niche = df.groupby(COL_NICHE, observed=True)[COL_CLIENT].nunique().to_dict()

    np_ = df.groupby([COL_NICHE, COL_N3], observed=True).agg(
        client_count=(COL_CLIENT, "nunique"),
        total_qty=(COL_QTY, "sum"),
    ).reset_index()

    np_["total_clients_in_niche"] = np_[COL_NICHE].map(total_clients_per_niche).astype(int)
    np_["niche_pct"] = (np_["client_count"] / np_["total_clients_in_niche"] * 100).round(1)
    np_["avg_qty_per_client"] = (np_["total_qty"] / np_["client_count"]).round(3)

    # Typical N4 items per niche+N3
    niche_n4_top = (
        df.groupby([COL_NICHE, COL_N3, COL_N4], observed=True)
        .agg(n4_clients=(COL_CLIENT, "nunique"))
        .reset_index()
        .sort_values("n4_clients", ascending=False)
        .groupby([COL_NICHE, COL_N3], observed=True)[COL_N4]
        .apply(lambda x: x.head(3).tolist())
        .reset_index()
        .rename(columns={COL_N4: "typical_n4"})
    )
    np_ = np_.merge(niche_n4_top, on=[COL_NICHE, COL_N3], how="left")

    _niche_profile = np_

    # --- Summary ---
    date_min = df[COL_ORDER_DATE].min()
    date_max = df[COL_ORDER_DATE].max()
    _upload_info = {
        "rows": len(df),
        "clients": len(_clients),
        "niches": df[COL_NICHE].nunique(),
        "orders": df[COL_ORDER_NUM].nunique(),
        "products_n3": len(_products_n3),
        "products_n4": df[COL_N4].nunique(),
        "date_min": str(date_min.date()) if pd.notna(date_min) else None,
        "date_max": str(date_max.date()) if pd.notna(date_max) else None,
    }

    return _upload_info


def is_loaded() -> bool:
    return _raw_df is not None


def get_upload_info() -> dict | None:
    return _upload_info


def get_clients() -> list[str]:
    return _clients


def get_client_niche(client: str) -> str | None:
    return _client_niche.get(client)


def get_client_total_orders(client: str) -> int:
    return _total_orders_per_client.get(client, 0)


def get_products_n3() -> list[str]:
    return _products_n3


def get_products_n4(n3: str) -> list[str]:
    return _products_n4_by_n3.get(n3, [])


def get_client_profile(client: str) -> pd.DataFrame:
    if _client_profile is None:
        return pd.DataFrame()
    return _client_profile[_client_profile[COL_CLIENT] == client]


def get_niche_profile(niche: str) -> pd.DataFrame:
    if _niche_profile is None:
        return pd.DataFrame()
    return _niche_profile[_niche_profile[COL_NICHE] == niche]
