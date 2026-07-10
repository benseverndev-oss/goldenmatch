from __future__ import annotations

from goldenflow._polars_lazy import pl


def read_database_columns(connection, query: str) -> dict[str, list]:
    """Read a query into a ``dict[str, list]`` via any PEP-249 (DBAPI 2.0) connection —
    **Polars-free** (stdlib DBAPI, no connectorx / no polars). ``connection`` is an OPEN
    DBAPI connection (``sqlite3.connect(...)``, ``psycopg2.connect(...)``, a SQLAlchemy
    raw connection, …); column names come from ``cursor.description`` and values are
    typed as the driver returns them. Feed the result straight to
    :func:`goldenflow.transform` for a Polars-free DB -> clean pipeline::

        import sqlite3, goldenflow
        from goldenflow.connectors.database import read_database_columns
        cols = read_database_columns(sqlite3.connect("app.db"), "SELECT * FROM customers")
        result = goldenflow.transform(cols, config=cfg)   # no Polars imported
    """
    cur = connection.cursor()
    try:
        cur.execute(query)
        names = [d[0] for d in cur.description]
        cols: dict[str, list] = {n: [] for n in names}
        for row in cur.fetchall():
            for i, n in enumerate(names):
                cols[n].append(row[i])
        return cols
    finally:
        cur.close()


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
    df.write_database(table, connection_string, if_table_exists="replace", **kwargs)
