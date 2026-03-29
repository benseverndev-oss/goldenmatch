"""Tests for detect_provider and extract_schema dispatch."""
from __future__ import annotations

import polars as pl
import pytest

from infermap.providers import detect_provider


def test_detect_csv():
    assert detect_provider("data/customers.csv") == "file"


def test_detect_parquet():
    assert detect_provider("data/customers.parquet") == "file"


def test_detect_xlsx():
    assert detect_provider("data/customers.xlsx") == "file"


def test_detect_postgresql():
    assert detect_provider("postgresql://user:pass@host/db") == "db"


def test_detect_sqlite():
    assert detect_provider("sqlite:///path/to/db.sqlite") == "db"


def test_detect_mysql():
    assert detect_provider("mysql://user:pass@host/db") == "db"


def test_detect_duckdb():
    assert detect_provider("duckdb:///path/to/db.duckdb") == "db"


def test_detect_yaml():
    assert detect_provider("schema/customers.yaml") == "schema_file"


def test_detect_yml():
    assert detect_provider("schema/customers.yml") == "schema_file"


def test_detect_json():
    assert detect_provider("schema/customers.json") == "schema_file"


def test_detect_polars_df():
    df = pl.DataFrame({"a": [1, 2, 3]})
    assert detect_provider(df) == "memory"


def test_detect_list_of_dicts():
    data = [{"name": "Alice", "age": 30}]
    assert detect_provider(data) == "memory"


def test_detect_unknown():
    assert detect_provider(12345) == "unknown"


def test_detect_unknown_string():
    assert detect_provider("some_random_source") == "unknown"
