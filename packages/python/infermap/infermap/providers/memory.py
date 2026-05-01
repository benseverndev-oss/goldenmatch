"""InMemoryProvider — accepts Polars DF, Pandas DF, or list[dict]."""
from __future__ import annotations

from typing import Any

import polars as pl

from infermap.types import FieldInfo, SchemaInfo
from infermap.providers.file import _normalize_dtype, _profile_series

_DEFAULT_SAMPLE_SIZE = 500


def _to_polars(source: Any) -> pl.DataFrame:
    """Convert source to a Polars DataFrame."""
    if isinstance(source, pl.DataFrame):
        return source

    # Detect Pandas DataFrame by duck-typing (avoid hard import dependency)
    if hasattr(source, "iloc") and hasattr(source, "to_dict"):
        return pl.from_pandas(source)

    if isinstance(source, list):
        if len(source) == 0:
            return pl.DataFrame()
        return pl.DataFrame(source)

    raise TypeError(f"InMemoryProvider cannot handle source of type {type(source)!r}")


class InMemoryProvider:
    """Extracts SchemaInfo from in-memory data: Polars DF, Pandas DF, or list[dict]."""

    def extract(
        self,
        source: Any,
        *,
        source_name: str = "memory",
        sample_size: int = _DEFAULT_SAMPLE_SIZE,
        **kwargs,
    ) -> SchemaInfo:
        df = _to_polars(source)

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

        return SchemaInfo(fields=fields, source_name=source_name)
