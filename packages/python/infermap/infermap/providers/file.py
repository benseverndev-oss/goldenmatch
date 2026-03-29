"""FileProvider — reads CSV, Parquet, and Excel files via Polars."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl

from infermap.errors import InferMapError
from infermap.types import FieldInfo, SchemaInfo

_DTYPE_MAP: dict[str, str] = {
    # Integer types
    "Int8": "integer",
    "Int16": "integer",
    "Int32": "integer",
    "Int64": "integer",
    "Int128": "integer",
    "UInt8": "integer",
    "UInt16": "integer",
    "UInt32": "integer",
    "UInt64": "integer",
    "UInt128": "integer",
    # Float types
    "Float32": "float",
    "Float64": "float",
    "Float16": "float",
    "Decimal": "float",
    # Boolean
    "Boolean": "boolean",
    # String / Categorical
    "String": "string",
    "Utf8": "string",
    "Categorical": "string",
    "Enum": "string",
    # Temporal
    "Date": "date",
    "Datetime": "datetime",
    "Time": "string",
    "Duration": "string",
}

_DEFAULT_SAMPLE_SIZE = 500


def _normalize_dtype(polars_dtype) -> str:
    """Map a Polars DataType instance to an infermap normalized dtype string."""
    name = polars_dtype.__class__.__name__
    return _DTYPE_MAP.get(name, "string")


def _profile_series(series: pl.Series, sample_size: int) -> dict:
    """Return profiling stats for a single Polars Series."""
    total = len(series)
    null_count = series.null_count()
    null_rate = null_count / total if total > 0 else 0.0

    non_null = series.drop_nulls()
    value_count = total
    unique_count = non_null.n_unique() if len(non_null) > 0 else 0
    unique_rate = unique_count / total if total > 0 else 0.0

    # Collect sample values (non-null, cast to string)
    sample = non_null.head(sample_size).cast(pl.String).to_list()

    return {
        "null_rate": null_rate,
        "unique_rate": unique_rate,
        "value_count": value_count,
        "sample_values": sample,
    }


class FileProvider:
    """Reads CSV, Parquet, and Excel files and returns a SchemaInfo."""

    def extract(self, source: Any, *, sample_size: int = _DEFAULT_SAMPLE_SIZE, **kwargs) -> SchemaInfo:
        path = Path(source)
        if not path.exists():
            raise InferMapError(f"File not found: {path}")

        suffix = path.suffix.lower()
        if suffix == ".csv":
            df = pl.read_csv(path, encoding="utf8", ignore_errors=True)
        elif suffix == ".parquet":
            df = pl.read_parquet(path)
        elif suffix in (".xlsx", ".xls"):
            try:
                df = pl.read_excel(path, engine="openpyxl")
            except ImportError as exc:
                raise InferMapError(
                    "openpyxl is required to read Excel files. "
                    "Install it with: pip install openpyxl"
                ) from exc
        else:
            raise InferMapError(f"Unsupported file format: {suffix}")

        fields = []
        for col_name, dtype in df.schema.items():
            stats = _profile_series(df[col_name], sample_size)
            fields.append(
                FieldInfo(
                    name=col_name,
                    dtype=_normalize_dtype(dtype),
                    sample_values=stats["sample_values"],
                    null_rate=stats["null_rate"],
                    unique_rate=stats["unique_rate"],
                    value_count=stats["value_count"],
                )
            )

        return SchemaInfo(fields=fields, source_name=path.stem)
