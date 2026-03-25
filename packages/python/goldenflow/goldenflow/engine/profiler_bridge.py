from __future__ import annotations

import re
from dataclasses import dataclass, field

import polars as pl

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_RE = re.compile(r"^[\+\(]?[\d][\d\(\)\-\.\s]{6,18}\d$")
_DATE_RE = re.compile(
    r"^(\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|"
    r"[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})$"
)
_NAME_RE = re.compile(r"^[A-Z][a-z]+(\s+[A-Z][a-z]+)+$")
_ZIP_RE = re.compile(r"^\d{5}(-\d{4})?$")

# Column name patterns for semantic type override
_NAME_PATTERNS: dict[str, list[str]] = {
    "zip": ["zip", "postal", "zipcode", "zip_code", "postal_code"],
    "phone": ["phone", "tel", "mobile", "cell", "fax"],
    "email": ["email", "e_mail", "mail"],
    "date": ["date", "created", "updated", "timestamp", "dob", "birth"],
    "state": ["state", "province", "region"],
    "name": ["name", "first_name", "last_name", "fname", "lname"],
}


def _override_type_by_column_name(column_name: str, current_type: str) -> str:
    """Override inferred type based on column name heuristics.

    Only overrides when the current type is generic (string/numeric) and the
    column name strongly suggests a specific semantic type.
    """
    if current_type not in ("string", "numeric"):
        return current_type  # don't override already-specific types

    col_lower = column_name.lower().replace("-", "_")
    for semantic_type, patterns in _NAME_PATTERNS.items():
        for pattern in patterns:
            if pattern in col_lower:
                return semantic_type

    return current_type


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

    # Check patterns against sample — order matters (more specific first)
    checks = [
        ("email", _EMAIL_RE, 0.7),
        ("zip", _ZIP_RE, 0.7),
        ("date", _DATE_RE, 0.5),
        ("phone", _PHONE_RE, 0.6),
        ("name", _NAME_RE, 0.5),
    ]
    for type_name, pattern, threshold in checks:
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

    inferred = _infer_type(series)
    inferred = _override_type_by_column_name(series.name, inferred)

    return ColumnProfile(
        name=series.name,
        inferred_type=inferred,
        row_count=row_count,
        null_count=null_count,
        null_pct=null_count / row_count if row_count > 0 else 0.0,
        unique_count=unique_count,
        unique_pct=unique_count / row_count if row_count > 0 else 0.0,
        sample_values=sample,
    )


def profile_dataframe(df: pl.DataFrame, file_path: str = "", use_llm: bool | None = None) -> DatasetProfile:
    """Profile a DataFrame. Uses GoldenCheck if available, otherwise falls back to built-in.

    When use_llm is None (default), auto-detects: uses LLM if OPENAI_API_KEY or
    ANTHROPIC_API_KEY is set in the environment. Set explicitly to True/False to override.
    """
    try:
        from goldencheck import scan_file
        from goldencheck.models.profile import DatasetProfile as GCProfile

        # If we have a file path and GoldenCheck, use it
        if file_path:
            # Determine whether to use LLM-enhanced scanning
            if use_llm is None:
                import os
                use_llm = bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"))

            if use_llm:
                try:
                    from goldencheck import scan_file_with_llm
                    provider = "openai" if os.environ.get("OPENAI_API_KEY") else "anthropic"
                    findings, gc_profile = scan_file_with_llm(file_path, provider=provider)
                except Exception:
                    # Fall back to non-LLM scan if LLM fails
                    findings, gc_profile = scan_file(file_path)
            else:
                findings, gc_profile = scan_file(file_path)
            columns = []
            for cp in gc_profile.columns:
                # Map GoldenCheck types to our semantic types
                # GoldenCheck returns "String", "Integer", etc. — we need "string", "phone", "email"
                gc_type = (cp.inferred_type or "").lower()
                # For generic string types, use our regex-based semantic inference
                if gc_type in ("string", "str", "utf8"):
                    series = df[cp.name] if cp.name in df.columns else None
                    semantic_type = _infer_type(series) if series is not None else "string"
                elif gc_type in ("integer", "int", "int64", "i64"):
                    semantic_type = "numeric"
                elif gc_type in ("float", "float64", "f64", "number", "numeric"):
                    semantic_type = "numeric"
                elif gc_type in ("boolean", "bool"):
                    semantic_type = "boolean"
                elif gc_type in ("date", "datetime"):
                    semantic_type = "date"
                else:
                    semantic_type = gc_type
                # Apply column name heuristic override
                semantic_type = _override_type_by_column_name(cp.name, semantic_type)
                columns.append(ColumnProfile(
                    name=cp.name,
                    inferred_type=semantic_type,
                    row_count=cp.row_count,
                    null_count=cp.null_count,
                    null_pct=cp.null_pct,
                    unique_count=cp.unique_count,
                    unique_pct=cp.unique_pct,
                    sample_values=[str(v) for v, _ in (cp.top_values or [])[:5]],
                    detected_format=cp.detected_format,
                ))
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
