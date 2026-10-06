"""Profile messy tabular data (CSV / Excel) into a compact, LLM-friendly description.

Handles: multiple sheets, junk rows above the real header, blank/duplicate column names,
mixed types, and gives per-column semantic type guesses, fill rates, cardinality,
candidate keys and possible duplicate records.
"""

import io
import re
from pathlib import Path
from typing import Any

import pandas as pd

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^\+?[\d\s().-]{7,20}$")
URL_RE = re.compile(r"^(https?://|www\.)\S+$", re.I)
CURRENCY_RE = re.compile(r"^\s*[$€£₹¥]\s?-?[\d,]+(\.\d+)?\s*$|^\s*-?[\d,]+(\.\d+)?\s?(USD|EUR|INR|GBP|Rs\.?)\s*$", re.I)
PERCENT_RE = re.compile(r"^\s*-?\d+(\.\d+)?\s*%\s*$")
BOOL_VALUES = {"yes", "no", "y", "n", "true", "false", "0", "1", "active", "inactive"}

TABULAR_EXTENSIONS = {".csv", ".tsv", ".xlsx", ".xlsm", ".xls"}
MAX_SHEETS = 20
MAX_COLUMNS = 200


def _read_frames(filename: str, data: bytes, max_rows: int) -> dict[str, pd.DataFrame]:
    ext = Path(filename).suffix.lower()
    if ext in {".csv", ".tsv"}:
        sep = "\t" if ext == ".tsv" else None
        for encoding in ("utf-8-sig", "latin-1"):
            try:
                df = pd.read_csv(
                    io.BytesIO(data),
                    sep=sep,
                    engine="python",
                    header=None,
                    dtype=str,
                    nrows=max_rows + 20,
                    encoding=encoding,
                    on_bad_lines="skip",
                )
                return {"Sheet1": df}
            except UnicodeDecodeError:
                continue
        raise ValueError("Could not decode CSV file")
    sheets = pd.read_excel(io.BytesIO(data), sheet_name=None, header=None, dtype=str, nrows=max_rows + 20)
    return dict(list(sheets.items())[:MAX_SHEETS])


def _detect_header_row(raw: pd.DataFrame) -> int:
    """Pick the first row among the top 15 that looks like a header (mostly non-empty short strings)."""
    best_row, best_score = 0, -1.0
    width = max(raw.shape[1], 1)
    for i in range(min(15, len(raw))):
        row = raw.iloc[i]
        values = [str(v).strip() for v in row if pd.notna(v) and str(v).strip()]
        if not values:
            continue
        filled = len(values) / width
        texty = sum(1 for v in values if not re.fullmatch(r"[\d.,\s/:-]+", v)) / len(values)
        unique = len(set(values)) / len(values)
        score = filled * 0.5 + texty * 0.3 + unique * 0.2
        if score > best_score + 0.05:
            best_row, best_score = i, score
    return best_row


def _clean_frame(raw: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    raw = raw.dropna(how="all").dropna(axis=1, how="all")
    if raw.empty:
        return raw, 0
    raw = raw.reset_index(drop=True)
    header_idx = _detect_header_row(raw)
    headers, seen = [], {}
    for i, h in enumerate(raw.iloc[header_idx].tolist()):
        name = str(h).strip() if pd.notna(h) and str(h).strip() else f"column_{i + 1}"
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 1
        headers.append(name)
    df = raw.iloc[header_idx + 1 :].copy()
    df.columns = headers
    df = df.dropna(how="all")
    df = df.apply(lambda s: s.map(lambda v: v.strip() if isinstance(v, str) else v))
    df = df.replace({"": None, "nan": None, "NaN": None, "N/A": None, "n/a": None, "-": None, "NULL": None})
    return df.iloc[:, :MAX_COLUMNS], header_idx


def _ratio(values: pd.Series, pattern: re.Pattern) -> float:
    return float(values.map(lambda v: bool(pattern.match(v))).mean()) if len(values) else 0.0


def _infer_type(name: str, values: pd.Series, distinct: int, total: int) -> str:
    if not len(values):
        return "empty"
    lname = name.lower()
    if _ratio(values, EMAIL_RE) > 0.8:
        return "email"
    if _ratio(values, URL_RE) > 0.8:
        return "url"
    if _ratio(values, PERCENT_RE) > 0.8:
        return "percent"
    if _ratio(values, CURRENCY_RE) > 0.8:
        return "currency"
    lowered = values.str.lower()
    if distinct <= 3 and lowered.isin(BOOL_VALUES).mean() > 0.95:
        return "boolean"
    numeric = pd.to_numeric(values.str.replace(",", "", regex=False), errors="coerce")
    numeric_ratio = float(numeric.notna().mean())
    phone_hint = any(k in lname for k in ("phone", "mobile", "contact no", "whatsapp", "tel"))
    if (phone_hint or numeric_ratio < 0.9) and _ratio(values, PHONE_RE) > 0.8 and values.str.len().mean() >= 8:
        return "phone"
    if numeric_ratio > 0.95:
        if any(k in lname for k in ("amount", "price", "cost", "value", "revenue", "fee", "salary", "total")):
            return "currency"
        if distinct == total and (numeric % 1 == 0).all() and any(k in lname for k in ("id", "no", "number", "code")):
            return "identifier"
        return "number"
    if any(k in lname for k in ("date", "dob", "created", "updated", "time", "on", "at")) or numeric_ratio < 0.2:
        parsed = pd.to_datetime(values, errors="coerce", format="mixed", dayfirst=True)
        if parsed.notna().mean() > 0.85:
            has_time = (parsed.dropna().dt.hour + parsed.dropna().dt.minute).gt(0).mean() > 0.3
            return "datetime" if has_time else "date"
    avg_len = values.str.len().mean()
    if avg_len > 120:
        return "long_text"
    if distinct <= max(12, int(total * 0.05)) and total >= 10 and avg_len < 40:
        return "picklist"
    if distinct == total and total > 5 and avg_len < 40:
        return "identifier"
    return "text"


def _mask(value: str, semantic: str) -> str:
    if semantic == "email" and "@" in value:
        local, domain = value.split("@", 1)
        return f"{local[:1]}***@{domain}"
    if semantic == "phone":
        digits = re.sub(r"\D", "", value)
        return f"***{digits[-3:]}" if len(digits) > 3 else "***"
    return value


def profile_frame(df: pd.DataFrame, mask_pii: bool) -> dict[str, Any]:
    total = len(df)
    columns = []
    for col in df.columns:
        series = df[col]
        values = series.dropna().astype(str)
        distinct = int(values.nunique())
        semantic = _infer_type(str(col), values, distinct, len(values))
        top = values.value_counts().head(12)
        samples = [str(v)[:80] for v in values.drop_duplicates().head(5)]
        if mask_pii and semantic in {"email", "phone"}:
            samples = [_mask(s, semantic) for s in samples]
        column: dict[str, Any] = {
            "name": str(col),
            "inferred_type": semantic,
            "fill_rate": round(len(values) / total, 3) if total else 0,
            "distinct": distinct,
            "samples": samples,
        }
        if semantic in {"picklist", "boolean"}:
            column["top_values"] = {str(k)[:60]: int(v) for k, v in top.items()}
        if semantic in {"number", "currency"}:
            nums = pd.to_numeric(values.str.replace(r"[^\d.-]", "", regex=True), errors="coerce").dropna()
            if len(nums):
                column["min"], column["max"] = float(nums.min()), float(nums.max())
        if distinct == len(values) and len(values) == total and total > 1:
            column["candidate_key"] = True
        columns.append(column)

    duplicates = []
    for col in df.columns:
        sem = next(c["inferred_type"] for c in columns if c["name"] == str(col))
        if sem in {"email", "phone"}:
            normalized = df[col].dropna().astype(str).str.lower().str.replace(r"[\s().-]", "", regex=True)
            dup_count = int(normalized.duplicated().sum())
            if dup_count:
                duplicates.append({"column": str(col), "duplicate_values": dup_count})
    return {"row_count": total, "column_count": len(columns), "columns": columns, "possible_duplicates": duplicates}


def profile_tabular(filename: str, data: bytes, max_rows: int, mask_pii: bool) -> dict[str, Any]:
    frames = _read_frames(filename, data, max_rows)
    sheets = []
    for sheet_name, raw in frames.items():
        df, header_idx = _clean_frame(raw)
        if df.empty:
            continue
        truncated = len(df) > max_rows
        df = df.iloc[:max_rows]
        profile = profile_frame(df, mask_pii)
        profile.update({"sheet": str(sheet_name), "header_row": header_idx + 1, "truncated": truncated})
        sheets.append(profile)
    return {"type": "tabular", "sheets": sheets}


def load_key_values(filename: str, data: bytes, max_rows: int) -> dict[tuple[str, str], set[str]]:
    """Distinct values of identifier-like columns, for cross-file relationship detection."""
    out: dict[tuple[str, str], set[str]] = {}
    for sheet_name, raw in _read_frames(filename, data, max_rows).items():
        df, _ = _clean_frame(raw)
        for col in df.columns:
            values = df[col].dropna().astype(str).str.strip().str.lower()
            if 1 < values.nunique() <= 100_000 and values.str.len().mean() < 60:
                out[(str(sheet_name), str(col))] = set(values.unique())
    return out
