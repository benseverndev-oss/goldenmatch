"""Database scanner — scan tables directly from Postgres, Snowflake, BigQuery, or any SQLAlchemy URL."""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import polars as pl

from goldencheck.engine.scanner import scan_file
from goldencheck.engine.confidence import apply_confidence_downgrade
from goldencheck.models.finding import Finding
from goldencheck.models.profile import DatasetProfile

logger = logging.getLogger(__name__)

__all__ = ["scan_database"]


def scan_database(
    connection_string: str,
    table: str | None = None,
    query: str | None = None,
    sample_size: int = 100_000,
    domain: str | None = None,
) -> tuple[list[Finding], DatasetProfile]:
    """Scan a database table or query result.

    Args:
        connection_string: Database URL (postgres://, snowflake://, bigquery://, etc.)
        table: Table name to scan (SELECT * FROM table LIMIT sample_size)
        query: Custom SQL query (overrides table)
        sample_size: Max rows to fetch
        domain: Domain pack name

    Returns:
        (findings, profile) tuple
    """
    if not table and not query:
        raise ValueError("Either 'table' or 'query' must be provided.")

    sql = query or f"SELECT * FROM {table} LIMIT {sample_size}"

    # Try Polars native connectors first
    df = _read_sql(connection_string, sql)

    # Write to temp CSV and scan (reuses the full profiler pipeline)
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as tmp:
        df.write_csv(tmp.name)
        tmp_path = Path(tmp.name)

    try:
        findings, profile = scan_file(tmp_path, sample_size=sample_size, domain=domain)
        findings = apply_confidence_downgrade(findings, llm_boost=False)
    finally:
        tmp_path.unlink(missing_ok=True)

    # Fix the file_path in profile to show the actual source
    source = table or "custom query"
    profile = DatasetProfile(
        file_path=f"{_mask_password(connection_string)}:{source}",
        row_count=profile.row_count,
        column_count=profile.column_count,
        columns=profile.columns,
    )

    return findings, profile


def _read_sql(connection_string: str, sql: str) -> pl.DataFrame:
    """Read SQL query into a Polars DataFrame."""
    # Try connectorx (fastest, supports most databases)
    try:
        import connectorx as cx
        return cx.read_sql(connection_string, sql, return_type="polars")
    except ImportError:
        pass

    # Try SQLAlchemy + pandas fallback
    try:
        import sqlalchemy
        import pandas as pd
        engine = sqlalchemy.create_engine(connection_string)
        with engine.connect() as conn:
            pdf = pd.read_sql(sql, conn)
        return pl.from_pandas(pdf)
    except ImportError:
        pass

    raise ImportError(
        "Database scanning requires 'connectorx' or 'sqlalchemy+pandas'. "
        "Install with: pip install connectorx  (or)  pip install sqlalchemy pandas"
    )


def _mask_password(url: str) -> str:
    """Mask password in connection string for display."""
    import re
    return re.sub(r'://([^:]+):([^@]+)@', r'://\1:***@', url)
