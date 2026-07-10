"""Smart sampling for large datasets."""
from __future__ import annotations

from goldencheck._polars_lazy import pl


def maybe_sample(df: pl.DataFrame, max_rows: int = 100_000) -> pl.DataFrame:
    if len(df) <= max_rows:
        return df
    return df.sample(n=max_rows, seed=42)
