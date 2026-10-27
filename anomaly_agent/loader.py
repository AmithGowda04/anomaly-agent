"""Load an Excel/CSV report and run data-quality checks.

Broken input must be reported as a *data problem*, not silently turned into fake
anomalies, so every check here produces an explicit issue."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import numpy as np
import pandas as pd

from .schema import normalize_name

DATE_HINTS = ("date", "day", "week", "month", "period", "time")


@dataclass
class Issue:
    level: str  # "error" | "warning" | "info"
    code: str
    message: str


@dataclass
class DataQuality:
    issues: list[Issue] = field(default_factory=list)
    date_column: Optional[str] = None
    frequency_days: Optional[float] = None

    def add(self, level: str, code: str, message: str) -> None:
        self.issues.append(Issue(level, code, message))

    @property
    def has_errors(self) -> bool:
        return any(i.level == "error" for i in self.issues)

    def lines(self) -> list[str]:
        return [f"[{i.level}] {i.message}" for i in self.issues]


def read_any(path_or_buffer, sheet: int | str = 0, name: str = "") -> pd.DataFrame:
    label = (name or str(path_or_buffer)).lower()
    if label.endswith((".csv", ".txt")):
        return pd.read_csv(path_or_buffer)
    return pd.read_excel(path_or_buffer, sheet_name=sheet)


def _to_numeric(s: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(s):
        return s.astype("float64")
    cleaned = s.astype("string").str.replace(r"[,₹$€%\s]", "", regex=True)
    return pd.to_numeric(cleaned, errors="coerce")


def _find_date_column(df: pd.DataFrame, requested: Optional[str]) -> Optional[str]:
    if requested and requested in df.columns:
        return requested
    for col in df.columns:  # prefer hinted names
        if any(h in col for h in DATE_HINTS) and _date_ratio(df[col]) >= 0.8:
            return col
    for col in df.columns:
        if _date_ratio(df[col]) >= 0.8 and not pd.api.types.is_float_dtype(df[col]):
            return col
    return None


def _date_ratio(s: pd.Series) -> float:
    if pd.api.types.is_numeric_dtype(s):
        return 0.0
    parsed = pd.to_datetime(s, errors="coerce", format="mixed")
    return float(parsed.notna().mean()) if len(s) else 0.0


def prepare(
    raw: pd.DataFrame,
    date_column: Optional[str] = None,
    column_map: Optional[dict[str, str]] = None,
) -> tuple[pd.DataFrame, DataQuality]:
    """Return (numeric frame indexed by date, DataQuality).

    column_map maps messy headers to clean names, e.g. {"Rev (INR)": "revenue"}.
    """
    dq = DataQuality()
    df = raw.copy()

    mapping = {normalize_name(k): normalize_name(v) for k, v in (column_map or {}).items()}
    renamed: dict[str, str] = {}
    for c in df.columns:
        n = normalize_name(c)
        renamed[c] = mapping.get(n, n)
    df = df.rename(columns=renamed)

    if df.columns.duplicated().any():
        dups = sorted(set(df.columns[df.columns.duplicated()]))
        dq.add("error", "duplicate_columns", f"Duplicate column names after cleaning: {dups}")
        return pd.DataFrame(), dq
    if df.empty:
        dq.add("error", "empty", "The file has no rows.")
        return pd.DataFrame(), dq

    req = normalize_name(date_column) if date_column else None
    dcol = _find_date_column(df, req)
    if dcol is None:
        dq.add("error", "no_date_column", "Could not find a date column.")
        return pd.DataFrame(), dq
    dq.date_column = dcol

    dates = pd.to_datetime(df[dcol], errors="coerce", format="mixed")
    bad = int(dates.isna().sum())
    if bad:
        dq.add("warning", "bad_dates", f"{bad} row(s) had unreadable dates and were dropped.")
    df = df.assign(**{dcol: dates}).dropna(subset=[dcol])

    dup = int(df[dcol].duplicated(keep="last").sum())
    if dup:
        dq.add("warning", "duplicate_dates", f"{dup} duplicate date row(s); kept the last of each.")
        df = df.drop_duplicates(subset=[dcol], keep="last")

    df = df.sort_values(dcol).set_index(dcol)
    df.index.name = "date"

    numeric: dict[str, pd.Series] = {}
    for c in df.columns:
        s = _to_numeric(df[c])
        if s.notna().mean() >= 0.5:
            numeric[c] = s
        else:
            dq.add("info", "dropped_column", f"Column '{c}' is not numeric and was ignored.")
    out = pd.DataFrame(numeric, index=df.index)
    if out.empty:
        dq.add("error", "no_numeric", "No numeric metric columns found.")
        return out, dq

    for c in out.columns:
        n_na = int(out[c].isna().sum())
        if n_na:
            dq.add("warning", "missing_values", f"'{c}' has {n_na} missing value(s); those days are skipped.")
        if out[c].nunique(dropna=True) <= 1:
            dq.add("warning", "constant_column", f"'{c}' never changes, so deviation rules cannot score it.")

    if len(out) >= 3:
        steps = out.index.to_series().diff().dropna().dt.days
        med = float(steps.median())
        dq.frequency_days = med
        if med == 1.0:
            full = pd.date_range(out.index.min(), out.index.max(), freq="D")
            missing = full.difference(out.index)
            if len(missing):
                shown = ", ".join(d.strftime("%Y-%m-%d") for d in missing[:5])
                more = "" if len(missing) <= 5 else f" (+{len(missing) - 5} more)"
                dq.add("warning", "date_gaps", f"{len(missing)} missing day(s): {shown}{more}.")
    return out, dq


def check_freshness(dq: DataQuality, df: pd.DataFrame, today: date, max_age_days: int) -> None:
    if df.empty:
        return
    age = (today - df.index.max().date()).days
    if age > max_age_days:
        dq.add(
            "warning",
            "stale_data",
            f"Latest row is {df.index.max().date()}, {age} day(s) old (limit {max_age_days}). "
            "Treat any 'all clear' with caution.",
        )
