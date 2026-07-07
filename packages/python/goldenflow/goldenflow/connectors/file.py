from __future__ import annotations

from pathlib import Path

from goldenflow._polars_lazy import pl

# Polars reader by suffix, resolved LAZILY at call time (a module-level
# ``pl.read_csv`` reference would import Polars at module load, defeating the lazy
# proxy — Phase 4a). Values are attribute names on the Polars module.
_READER_NAMES: dict[str, str] = {
    ".csv": "read_csv",
    ".parquet": "read_parquet",
    ".json": "read_json",
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
    reader_name = _READER_NAMES.get(suffix)
    if reader_name is None:
        raise ValueError(f"Unsupported file format: {suffix}")
    return getattr(pl, reader_name)(path, **kwargs)


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
