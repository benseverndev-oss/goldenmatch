from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import polars as pl

_READERS: dict[str, Callable] = {
    ".csv": pl.read_csv,
    ".parquet": pl.read_parquet,
    ".json": pl.read_json,
}

_WRITERS: dict[str, str] = {
    ".csv": "write_csv",
    ".parquet": "write_parquet",
    ".json": "write_json",
}


def read_file(path: Path, **kwargs) -> pl.DataFrame:
    """Read a data file into a Polars DataFrame."""
    suffix = path.suffix.lower()
    if suffix == ".xlsx":
        try:
            import openpyxl  # noqa: F401

            return pl.read_excel(path, **kwargs)
        except ImportError:
            raise ImportError(
                "openpyxl is required for Excel files: pip install goldenflow[excel]"
            )
    reader = _READERS.get(suffix)
    if reader is None:
        raise ValueError(f"Unsupported file format: {suffix}")
    return reader(path, **kwargs)


def write_file(df: pl.DataFrame, path: Path, **kwargs) -> None:
    """Write a Polars DataFrame to a file."""
    suffix = path.suffix.lower()
    if suffix == ".xlsx":
        try:
            import openpyxl  # noqa: F401

            df.write_excel(path, **kwargs)
            return
        except ImportError:
            raise ImportError(
                "openpyxl is required for Excel files: pip install goldenflow[excel]"
            )
    writer_method = _WRITERS.get(suffix)
    if writer_method is None:
        raise ValueError(f"Unsupported file format: {suffix}")
    getattr(df, writer_method)(path, **kwargs)
