from __future__ import annotations

import re
from dataclasses import dataclass, field

import polars as pl

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_RE = re.compile(r"^[\+\(\)\-\.\s\d]{7,20}$")
_DATE_RE = re.compile(
    r"^(\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|"
    r"[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})$"
)
_NAME_RE = re.compile(r"^[A-Z][a-z]+(\s+[A-Z][a-z]+)+$")


@dataclass
class ColumnProfile:
    name: str
    inferred_type: str
    row_count: int
    null_count: int
    null_pct: float
    unique_count: int
    unique_pct: float
    sample_values: list[str] = field(default_factory=list)
    detected_format: str | None = None


@dataclass
class DatasetProfile:
    file_path: str
    row_count: int
    column_count: int
    columns: list[ColumnProfile]


def _infer_type(series: pl.Series) -> str:
    """Infer semantic type from a string series using regex heuristics."""
    if series.dtype in (pl.Int8, pl.Int16, pl.Int32, pl.Int64,
                         pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64,
                         pl.Float32, pl.Float64):
        return "numeric"
    if series.dtype == pl.Boolean:
        return "boolean"
    if series.dtype in (pl.Date, pl.Datetime):
        return "date"

    # Sample non-null string values for pattern matching
    non_null = series.drop_nulls().cast(pl.Utf8)
    if len(non_null) == 0:
        return "string"

    sample = non_null.head(min(100, len(non_null))).to_list()
    sample_stripped = [s.strip() for s in sample if s and s.strip()]
    if not sample_stripped:
        return "string"

    # Check patterns against sample
    checks = {
        "email": (_EMAIL_RE, 0.7),
        "phone": (_PHONE_RE, 0.6),
        "date": (_DATE_RE, 0.5),
        "name": (_NAME_RE, 0.5),
    }
    for type_name, (pattern, threshold) in checks.items():
        match_pct = sum(1 for v in sample_stripped if pattern.match(v)) / len(sample_stripped)
        if match_pct >= threshold:
            return type_name

    return "string"


def _profile_column(series: pl.Series) -> ColumnProfile:
    row_count = len(series)
    null_count = series.null_count()
    non_null = series.drop_nulls()
    unique_count = non_null.n_unique()
    sample = non_null.head(5).cast(pl.Utf8).to_list() if len(non_null) > 0 else []

    return ColumnProfile(
        name=series.name,
        inferred_type=_infer_type(series),
        row_count=row_count,
        null_count=null_count,
        null_pct=null_count / row_count if row_count > 0 else 0.0,
        unique_count=unique_count,
        unique_pct=unique_count / row_count if row_count > 0 else 0.0,
        sample_values=sample,
    )


def profile_dataframe(df: pl.DataFrame, file_path: str = "") -> DatasetProfile:
    """Profile a DataFrame. Uses GoldenCheck if available, otherwise falls back to built-in."""
    try:
        from goldencheck import scan_file
        from goldencheck.models.profile import DatasetProfile as GCProfile

        # If we have a file path and GoldenCheck, use it
        if file_path:
            findings, gc_profile = scan_file(file_path)
            columns = [
                ColumnProfile(
                    name=cp.name,
                    inferred_type=cp.inferred_type,
                    row_count=cp.row_count,
                    null_count=cp.null_count,
                    null_pct=cp.null_pct,
                    unique_count=cp.unique_count,
                    unique_pct=cp.unique_pct,
                    sample_values=[str(v) for v, _ in (cp.top_values or [])[:5]],
                    detected_format=cp.detected_format,
                )
                for cp in gc_profile.columns
            ]
            return DatasetProfile(
                file_path=gc_profile.file_path,
                row_count=gc_profile.row_count,
                column_count=gc_profile.column_count,
                columns=columns,
            )
    except ImportError:
        pass  # Fall back to built-in profiler

    # Built-in fallback profiler
    columns = [_profile_column(df[col]) for col in df.columns]
    return DatasetProfile(
        file_path=file_path,
        row_count=df.shape[0],
        column_count=df.shape[1],
        columns=columns,
    )
