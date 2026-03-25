import pytest

from goldenflow.connectors.database import read_table, write_table


def test_read_table_requires_connectorx():
    with pytest.raises((ImportError, NotImplementedError)):
        read_table("postgresql://localhost/test", "users")


def test_write_table_requires_connectorx():
    import polars as pl
    df = pl.DataFrame({"a": [1]})
    with pytest.raises((ImportError, NotImplementedError)):
        write_table(df, "postgresql://localhost/test", "users")
