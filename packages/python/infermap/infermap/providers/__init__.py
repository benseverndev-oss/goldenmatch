"""infermap providers — auto-detection and dispatch."""
from __future__ import annotations

from typing import Any

from infermap.types import SchemaInfo

_FILE_SUFFIXES = {".csv", ".parquet", ".xlsx", ".xls"}
_DB_PREFIXES = ("postgresql://", "postgres://", "sqlite://", "mysql://", "duckdb://")
_SCHEMA_SUFFIXES = {".yaml", ".yml", ".json"}


def detect_provider(source: Any) -> str:
    """Return a string key identifying which provider should handle *source*.

    Keys: "file", "db", "schema_file", "memory", "unknown"
    """
    if isinstance(source, str):
        lower = source.lower()
        # Check DB URI prefixes first
        for prefix in _DB_PREFIXES:
            if lower.startswith(prefix):
                return "db"
        # Check file suffixes
        from pathlib import PurePosixPath
        suffix = PurePosixPath(lower).suffix
        if suffix in _FILE_SUFFIXES:
            return "file"
        if suffix in _SCHEMA_SUFFIXES:
            return "schema_file"
        return "unknown"

    # Path objects
    try:
        from pathlib import Path
        if isinstance(source, Path):
            suffix = source.suffix.lower()
            if suffix in _FILE_SUFFIXES:
                return "file"
            if suffix in _SCHEMA_SUFFIXES:
                return "schema_file"
            return "unknown"
    except Exception:
        pass

    # Polars DataFrame — check before list/dict since it has .columns
    try:
        import polars as pl
        if isinstance(source, pl.DataFrame):
            return "memory"
    except ImportError:
        pass

    # Pandas DataFrame (duck-typed) or list[dict]
    if hasattr(source, "columns") or (isinstance(source, list) and source and isinstance(source[0], dict)):
        return "memory"
    if isinstance(source, list):
        return "memory"

    return "unknown"


def extract_schema(source: Any, **kwargs) -> SchemaInfo:
    """Dispatch to the correct provider based on auto-detection."""
    kind = detect_provider(source)

    if kind == "file":
        from infermap.providers.file import FileProvider
        return FileProvider().extract(source, **kwargs)

    if kind == "memory":
        from infermap.providers.memory import InMemoryProvider
        return InMemoryProvider().extract(source, **kwargs)

    if kind == "schema_file":
        from infermap.providers.schema_file import SchemaFileProvider
        return SchemaFileProvider().extract(source, **kwargs)

    if kind == "db":
        from infermap.providers.db import DBProvider
        return DBProvider().extract(source, **kwargs)

    raise ValueError(f"Cannot determine provider for source: {source!r}")
