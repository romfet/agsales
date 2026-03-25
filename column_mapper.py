"""
Column mapper — automatically recognizes which Excel columns map to the expected schema.

Uses three strategies (in order of priority):
1. Exact match by name
2. Fuzzy match by name (synonyms, keywords, partial matches)
3. Content analysis (data types, cardinality, value patterns)
"""
import re
from difflib import SequenceMatcher
import pandas as pd

# Expected schema: internal_name -> description
EXPECTED_COLUMNS = {
    "Клиентская ниша": {
        "description": "Ниша / отрасль / сегмент клиента",
        "required": True,
        "keywords": ["ниша", "отрасль", "сегмент", "индустрия", "сфера", "направление", "категория клиент", "тип клиент", "группа клиент"],
    },
    "Клиент": {
        "description": "Название клиента / покупателя / контрагента",
        "required": True,
        "keywords": ["клиент", "покупатель", "контрагент", "заказчик", "компания", "организация", "партнер", "customer"],
    },
    "ЗаказНомер": {
        "description": "Номер заказа / документа / накладной",
        "required": True,
        "keywords": ["заказ", "номер", "документ", "накладная", "счет", "order", "номер заказа", "№ заказа", "док"],
    },
    "ЗаказДата": {
        "description": "Дата заказа / отгрузки",
        "required": True,
        "keywords": ["дата", "date", "заказдата", "дата заказа", "дата отгрузки", "дата документа"],
    },
    "N1 (товарный раздел)": {
        "description": "Товарный раздел (уровень 1 иерархии)",
        "required": False,
        "keywords": ["n1", "раздел", "товарный раздел", "section", "level1", "уровень 1"],
    },
    "N2 (товарная группа)": {
        "description": "Товарная группа (уровень 2 иерархии)",
        "required": False,
        "keywords": ["n2", "группа", "товарная группа", "group", "level2", "уровень 2"],
    },
    "N3 (товарная подгруппа)": {
        "description": "Товарная подгруппа (уровень 3 иерархии)",
        "required": True,
        "keywords": ["n3", "подгруппа", "товарная подгруппа", "subgroup", "level3", "уровень 3"],
    },
    "N4 (товар)": {
        "description": "Товар / номенклатура (уровень 4 иерархии)",
        "required": True,
        "keywords": ["n4", "товар", "номенклатура", "продукт", "product", "item", "sku", "level4", "уровень 4", "наименование"],
    },
    "Количество": {
        "description": "Количество (тонны / штуки)",
        "required": True,
        "keywords": ["количество", "кол-во", "qty", "quantity", "объем", "вес", "тонн", "масса", "amount"],
    },
}


def _normalize(s: str) -> str:
    """Lowercase, strip, collapse whitespace."""
    return re.sub(r"\s+", " ", str(s).strip().lower())


def _keyword_score(col_name: str, keywords: list[str]) -> float:
    """Score how well a column name matches a list of keywords. Returns 0.0-1.0."""
    norm = _normalize(col_name)
    best = 0.0
    for kw in keywords:
        kw_norm = _normalize(kw)
        # Exact keyword match
        if kw_norm == norm:
            return 1.0
        # Keyword is contained in column name
        if kw_norm in norm:
            score = len(kw_norm) / max(len(norm), 1)
            best = max(best, 0.5 + score * 0.4)
        # Column name is contained in keyword
        if norm in kw_norm:
            score = len(norm) / max(len(kw_norm), 1)
            best = max(best, 0.4 + score * 0.3)
        # Fuzzy similarity
        ratio = SequenceMatcher(None, norm, kw_norm).ratio()
        if ratio > 0.6:
            best = max(best, ratio * 0.7)
    return best


def _analyze_column_content(series: pd.Series) -> dict:
    """Analyze a column's content and return feature dict."""
    sample = series.dropna()
    total = len(sample)
    if total == 0:
        return {"dtype": "empty", "nunique": 0, "nunique_ratio": 0}

    nunique = sample.nunique()
    nunique_ratio = nunique / total

    features = {
        "nunique": nunique,
        "nunique_ratio": nunique_ratio,
        "total": total,
    }

    # Check if numeric
    if pd.api.types.is_numeric_dtype(series):
        features["dtype"] = "numeric"
        features["mean"] = sample.mean()
        features["has_decimals"] = (sample % 1 != 0).any()
        return features

    # Check if datetime
    if pd.api.types.is_datetime64_any_dtype(series):
        features["dtype"] = "datetime"
        return features

    # String analysis
    features["dtype"] = "string"
    str_sample = sample.astype(str)
    avg_len = str_sample.str.len().mean()
    features["avg_len"] = avg_len

    # Check if strings look like dates
    date_pattern = r"\d{2,4}[.\-/]\d{2}[.\-/]\d{2,4}"
    date_matches = str_sample.head(100).str.contains(date_pattern, regex=True).mean()
    features["date_like_ratio"] = date_matches

    # Check if strings look like order numbers (alphanumeric codes)
    code_pattern = r"^[A-ZА-Яa-zа-я]{2,6}[\-_]?\d{3,}"
    code_matches = str_sample.head(100).str.contains(code_pattern, regex=True).mean()
    features["code_like_ratio"] = code_matches

    # Check if strings contain hierarchy markers (e.g., "01.", "02.01.")
    hierarchy_pattern = r"^\d{1,2}\."
    hier_matches = str_sample.head(100).str.contains(hierarchy_pattern, regex=True).mean()
    features["hierarchy_ratio"] = hier_matches

    # Check for numeric prefix pattern like "01. Name", "02.01. Name"
    prefix_dots = str_sample.head(100).str.count(r"\.").mean()
    features["avg_dots_in_value"] = prefix_dots

    return features


def _content_score(features: dict, expected_col: str) -> float:
    """Score how well column content matches an expected column role. Returns 0.0-1.0."""
    dtype = features.get("dtype", "empty")
    nunique_ratio = features.get("nunique_ratio", 0)
    nunique = features.get("nunique", 0)

    if expected_col == "Количество":
        if dtype == "numeric" and features.get("has_decimals"):
            return 0.9
        if dtype == "numeric":
            return 0.6
        return 0.0

    if expected_col == "ЗаказДата":
        if dtype == "datetime":
            return 0.9
        if dtype == "string" and features.get("date_like_ratio", 0) > 0.5:
            return 0.7
        return 0.0

    if expected_col == "ЗаказНомер":
        if dtype == "string" and features.get("code_like_ratio", 0) > 0.5:
            return 0.8
        if dtype == "string" and nunique_ratio > 0.3:
            return 0.3
        return 0.0

    if expected_col == "Клиент":
        if dtype == "string" and 0.001 < nunique_ratio < 0.15:
            return 0.5
        if dtype == "string" and nunique < 500:
            return 0.3
        return 0.0

    if expected_col == "Клиентская ниша":
        if dtype == "string" and nunique < 30 and nunique_ratio < 0.01:
            return 0.7
        if dtype == "string" and nunique < 50:
            return 0.4
        return 0.0

    # N1-N4: hierarchy columns — distinguish by cardinality and depth
    if expected_col in ("N1 (товарный раздел)", "N2 (товарная группа)", "N3 (товарная подгруппа)", "N4 (товар)"):
        if dtype != "string":
            return 0.0
        hier_ratio = features.get("hierarchy_ratio", 0)
        avg_dots = features.get("avg_dots_in_value", 0)

        # Hierarchy columns typically have numbered prefixes
        if hier_ratio < 0.3:
            return 0.1

        # Distinguish N1/N2/N3/N4 by average dots in values (depth indicator)
        # N1: "01. Name" (~1 dot), N2: "01.01. Name" (~2 dots), etc.
        expected_depth = {
            "N1 (товарный раздел)": (0.5, 1.5),
            "N2 (товарная группа)": (1.5, 3.0),
            "N3 (товарная подгруппа)": (2.5, 5.0),
            "N4 (товар)": (0, 0),
        }
        if expected_col == "N4 (товар)":
            # N4 is the most granular — highest cardinality among hierarchy cols
            if nunique > 50:
                return 0.5 + min(hier_ratio, 0.3)
            return 0.2

        lo, hi = expected_depth[expected_col]
        if lo <= avg_dots <= hi:
            return 0.7
        # Close to range
        if lo - 1 <= avg_dots <= hi + 1:
            return 0.4
        return 0.2

    return 0.0


def detect_mapping(filepath: str, sample_rows: int = 500) -> dict:
    """
    Analyze an Excel file and return a proposed column mapping.

    Returns:
        {
            "columns": ["col1", "col2", ...],         # all columns in the file
            "mapping": {                               # proposed mapping
                "internal_name": {
                    "mapped_to": "original_col_name" | null,
                    "confidence": 0.0-1.0,
                    "method": "exact" | "keyword" | "content" | null,
                    "description": "...",
                    "required": bool,
                }
            },
            "unmapped_file_columns": ["col_x", ...],  # file columns not assigned
            "sample_data": {                           # first few values per column
                "col1": ["val1", "val2", ...],
            }
        }
    """
    df = pd.read_excel(filepath, sheet_name=0, nrows=sample_rows)
    file_columns = list(df.columns)

    # Analyze content of all columns
    content_features = {}
    for col in file_columns:
        content_features[col] = _analyze_column_content(df[col])

    # Build sample data (first 5 non-null values per column)
    sample_data = {}
    for col in file_columns:
        vals = df[col].dropna().head(5).tolist()
        sample_data[col] = [str(v) for v in vals]

    # Score all (expected_col, file_col) pairs
    scores = {}
    for exp_name, exp_info in EXPECTED_COLUMNS.items():
        scores[exp_name] = {}
        for fcol in file_columns:
            # Strategy 1: exact match
            if _normalize(fcol) == _normalize(exp_name):
                scores[exp_name][fcol] = (1.0, "exact")
                continue

            # Strategy 2: keyword match
            kw_score = _keyword_score(fcol, exp_info["keywords"])

            # Strategy 3: content analysis
            ct_score = _content_score(content_features[fcol], exp_name)

            # Combined score: keyword match is more reliable, but content helps disambiguate
            if kw_score >= 0.5:
                combined = kw_score * 0.7 + ct_score * 0.3
                method = "keyword"
            elif ct_score >= 0.5:
                combined = kw_score * 0.3 + ct_score * 0.7
                method = "content"
            else:
                combined = max(kw_score, ct_score) * 0.8
                method = "keyword" if kw_score >= ct_score else "content"

            scores[exp_name][fcol] = (combined, method)

    # Greedy assignment: assign best scores first, no file column used twice
    used_file_cols = set()
    mapping = {}

    # Sort all pairs by score descending
    all_pairs = []
    for exp_name in EXPECTED_COLUMNS:
        for fcol, (score, method) in scores[exp_name].items():
            all_pairs.append((score, exp_name, fcol, method))
    all_pairs.sort(key=lambda x: -x[0])

    assigned_expected = set()
    for score, exp_name, fcol, method in all_pairs:
        if exp_name in assigned_expected or fcol in used_file_cols:
            continue
        if score < 0.15:
            continue
        mapping[exp_name] = {
            "mapped_to": fcol,
            "confidence": round(score, 2),
            "method": method,
            "description": EXPECTED_COLUMNS[exp_name]["description"],
            "required": EXPECTED_COLUMNS[exp_name]["required"],
        }
        assigned_expected.add(exp_name)
        used_file_cols.add(fcol)

    # Fill in unmatched expected columns
    for exp_name, exp_info in EXPECTED_COLUMNS.items():
        if exp_name not in mapping:
            mapping[exp_name] = {
                "mapped_to": None,
                "confidence": 0.0,
                "method": None,
                "description": exp_info["description"],
                "required": exp_info["required"],
            }

    unmapped = [c for c in file_columns if c not in used_file_cols]

    return {
        "columns": file_columns,
        "mapping": mapping,
        "unmapped_file_columns": unmapped,
        "sample_data": sample_data,
    }


def apply_mapping(filepath: str, mapping: dict[str, str]) -> pd.DataFrame:
    """
    Read Excel and rename columns according to the confirmed mapping.

    Args:
        filepath: path to Excel file
        mapping: {internal_name: original_col_name} — only non-null entries

    Returns:
        DataFrame with renamed columns matching the expected schema.
    """
    df = pd.read_excel(filepath, sheet_name=0)
    rename_map = {orig: internal for internal, orig in mapping.items() if orig}
    df = df.rename(columns=rename_map)
    return df
