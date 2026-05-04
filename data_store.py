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
_client_profile_n4: pd.DataFrame | None = None
_niche_profile_n4: pd.DataFrame | None = None
_clients: list[str] = []
_client_niche: dict[str, str] = {}
_products_n3: list[str] = []
_products_n4_by_n3: dict[str, list[str]] = {}
_total_orders_per_client: dict[str, int] = {}
_upload_info: dict | None = None

# Stock data (separate optional file "Остатки в выборке")
_stock_by_n4: dict[str, dict] = {}
_stock_info: dict | None = None


def load_excel(filepath: str, column_mapping: dict[str, str] | None = None) -> dict:
    """Parse Excel, build all aggregates. Returns summary stats.

    Args:
        filepath: path to Excel file.
        column_mapping: optional {internal_name: original_col_name} mapping.
            If provided, columns are renamed before validation.
    """
    global _raw_df, _client_profile, _niche_profile
    global _client_profile_n4, _niche_profile_n4
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

    # --- Build client profile at N4 (product) level ---
    cp_n4 = df.groupby([COL_CLIENT, COL_N3, COL_N4], observed=True).agg(
        order_count=(COL_ORDER_NUM, "nunique"),
        total_qty=(COL_QTY, "sum"),
    ).reset_index()

    cp_n4["total_orders"] = cp_n4[COL_CLIENT].map(_total_orders_per_client).astype(int)
    cp_n4["frequency_pct"] = (cp_n4["order_count"] / cp_n4["total_orders"] * 100).round(1)
    cp_n4["avg_qty_per_order"] = (cp_n4["total_qty"] / cp_n4["order_count"]).round(3)

    _client_profile_n4 = cp_n4

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

    # --- Build niche profile at N4 (product) level ---
    np_n4 = df.groupby([COL_NICHE, COL_N3, COL_N4], observed=True).agg(
        client_count=(COL_CLIENT, "nunique"),
        total_qty=(COL_QTY, "sum"),
    ).reset_index()

    np_n4["total_clients_in_niche"] = np_n4[COL_NICHE].map(total_clients_per_niche).astype(int)
    np_n4["niche_pct"] = (np_n4["client_count"] / np_n4["total_clients_in_niche"] * 100).round(1)
    np_n4["avg_qty_per_client"] = (np_n4["total_qty"] / np_n4["client_count"]).round(3)

    _niche_profile_n4 = np_n4

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


def get_client_profile_n4(client: str) -> pd.DataFrame:
    if _client_profile_n4 is None:
        return pd.DataFrame()
    return _client_profile_n4[_client_profile_n4[COL_CLIENT] == client]


def get_niche_profile_n4(niche: str) -> pd.DataFrame:
    if _niche_profile_n4 is None:
        return pd.DataFrame()
    return _niche_profile_n4[_niche_profile_n4[COL_NICHE] == niche]


def get_order_lines(order_num: str) -> list[dict]:
    """Get order lines by order number. Returns [{n3, n4, qty, client}, ...]."""
    if _raw_df is None:
        return []
    mask = _raw_df[COL_ORDER_NUM].astype(str) == str(order_num)
    order = _raw_df[mask]
    if order.empty:
        return []

    client = order[COL_CLIENT].iloc[0]
    lines = []
    for _, row in order.iterrows():
        lines.append({
            "n3": row[COL_N3],
            "n4": row[COL_N4] if pd.notna(row[COL_N4]) else None,
            "qty": round(float(row[COL_QTY]), 3) if pd.notna(row[COL_QTY]) else 0,
        })
    return {"client": client, "lines": lines}


def load_stock(filepath: str) -> dict:
    """Parse stock file ("Остатки в выборке"). Expected layout:
    sheet 0, header in 2nd row, columns: [empty | N4 | Остаток, тн | В пути, тн].
    Aggregates duplicates by N4 (sum on_stock and in_transit).
    """
    global _stock_by_n4, _stock_info

    df = pd.read_excel(filepath, sheet_name=0, header=1)
    # Drop fully empty leading column if present
    df = df.loc[:, ~df.columns.astype(str).str.startswith("Unnamed: 0")]

    # Identify columns by header text (tolerant to slight naming variations)
    cols = {str(c).strip().lower(): c for c in df.columns}
    n4_col = next((cols[k] for k in cols if k == "n4"), None)
    stock_col = next((cols[k] for k in cols if k.startswith("остаток")), None)
    transit_col = next((cols[k] for k in cols if "пути" in k), None)

    if not n4_col or (not stock_col and not transit_col):
        raise ValueError(
            "Не удалось распознать колонки файла остатков. "
            "Ожидаются: 'N4', 'Остаток, тн', 'В пути, тн'."
        )

    df = df[[c for c in [n4_col, stock_col, transit_col] if c is not None]].copy()
    df[n4_col] = df[n4_col].astype(str).str.strip()
    df = df[df[n4_col] != ""]
    df = df[df[n4_col].str.lower() != "nan"]

    if stock_col:
        df[stock_col] = pd.to_numeric(df[stock_col], errors="coerce")
    if transit_col:
        df[transit_col] = pd.to_numeric(df[transit_col], errors="coerce")

    stock_map: dict[str, dict] = {}
    for _, row in df.iterrows():
        name = row[n4_col]
        on = float(row[stock_col]) if stock_col and pd.notna(row[stock_col]) else None
        intr = float(row[transit_col]) if transit_col and pd.notna(row[transit_col]) else None
        if name in stock_map:
            cur = stock_map[name]
            cur["on_stock"] = (cur["on_stock"] or 0) + (on or 0) if (cur["on_stock"] is not None or on is not None) else None
            cur["in_transit"] = (cur["in_transit"] or 0) + (intr or 0) if (cur["in_transit"] is not None or intr is not None) else None
        else:
            stock_map[name] = {"on_stock": on, "in_transit": intr}

    _stock_by_n4 = stock_map
    _stock_info = {
        "rows": len(df),
        "products_n4": len(stock_map),
        "with_on_stock": sum(1 for v in stock_map.values() if v["on_stock"] is not None),
        "with_in_transit": sum(1 for v in stock_map.values() if v["in_transit"] is not None),
    }
    return _stock_info


def clear_stock() -> None:
    global _stock_by_n4, _stock_info
    _stock_by_n4 = {}
    _stock_info = None


def get_stock_info() -> dict | None:
    return _stock_info


def is_stock_loaded() -> bool:
    return _stock_info is not None


def get_stock_for_n4(n4: str) -> dict | None:
    """Returns {'on_stock': float|None, 'in_transit': float|None} or None if N4 absent."""
    if not n4 or not _stock_by_n4:
        return None
    return _stock_by_n4.get(str(n4).strip())


def search_orders(query: str, client: str = None) -> list[dict]:
    """Search order numbers by prefix. Returns [{order_num, date, line_count}, ...]."""
    if _raw_df is None:
        return []
    df = _raw_df
    if client:
        df = df[df[COL_CLIENT] == client]

    # Find matching order numbers
    order_nums = df[COL_ORDER_NUM].astype(str)
    mask = order_nums.str.contains(str(query), case=False, na=False)
    matched = df[mask]

    if matched.empty:
        return []

    result = (
        matched.groupby(COL_ORDER_NUM, observed=True)
        .agg(
            date=(COL_ORDER_DATE, "max"),
            line_count=(COL_N3, "count"),
            client=(COL_CLIENT, "first"),
        )
        .reset_index()
        .sort_values("date", ascending=False)
        .head(10)
    )

    return [
        {
            "order_num": str(row[COL_ORDER_NUM]),
            "date": str(row["date"].date()) if pd.notna(row["date"]) else None,
            "line_count": int(row["line_count"]),
            "client": row["client"],
        }
        for _, row in result.iterrows()
    ]
