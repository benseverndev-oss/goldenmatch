from __future__ import annotations

import polars as pl


def read_table(connection_string: str, table: str, **kwargs) -> pl.DataFrame:
    """Read a database table into a Polars DataFrame."""
    try:
        import connectorx  # noqa: F401
    except ImportError:
        raise ImportError("Database support requires: pip install goldenflow[db]")
    return pl.read_database(f"SELECT * FROM {table}", connection_string, **kwargs)


def write_table(df: pl.DataFrame, connection_string: str, table: str, **kwargs) -> None:
    """Write a Polars DataFrame to a database table."""
    try:
        import connectorx  # noqa: F401
    except ImportError:
        raise ImportError("Database support requires: pip install goldenflow[db]")
    raise NotImplementedError("Database writing is not yet implemented — use file export")
