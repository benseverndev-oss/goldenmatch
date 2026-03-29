"""Tests for InMemoryProvider."""
from __future__ import annotations

import pandas as pd
import polars as pl
import pytest

from infermap.providers.memory import InMemoryProvider


def make_polars_df():
    return pl.DataFrame(
        {
            "id": [1, 2, 3, 4],
            "name": ["Alice", "Bob", None, "Dave"],
            "score": [9.5, 8.0, 7.5, None],
        }
    )


def test_polars_df_accepted():
    provider = InMemoryProvider()
    schema = provider.extract(make_polars_df())
    names = [f.name for f in schema.fields]
    assert "id" in names
    assert "name" in names
    assert "score" in names


def test_pandas_df_accepted():
    pdf = pd.DataFrame({"x": [1, 2, 3], "y": ["a", "b", "c"]})
    provider = InMemoryProvider()
    schema = provider.extract(pdf)
    names = [f.name for f in schema.fields]
    assert "x" in names
    assert "y" in names


def test_list_of_dicts_accepted():
    data = [{"col_a": 1, "col_b": "hello"}, {"col_a": 2, "col_b": "world"}]
    provider = InMemoryProvider()
    schema = provider.extract(data)
    names = [f.name for f in schema.fields]
    assert "col_a" in names
    assert "col_b" in names


def test_null_rate_approximately_correct():
    """1 null in 4 rows => null_rate ~= 0.25."""
    provider = InMemoryProvider()
    schema = provider.extract(make_polars_df())
    name_field = next(f for f in schema.fields if f.name == "name")
    assert abs(name_field.null_rate - 0.25) < 0.01


def test_null_rate_score_field():
    provider = InMemoryProvider()
    schema = provider.extract(make_polars_df())
    score_field = next(f for f in schema.fields if f.name == "score")
    assert abs(score_field.null_rate - 0.25) < 0.01


def test_source_name_configurable():
    provider = InMemoryProvider()
    schema = provider.extract(make_polars_df(), source_name="my_dataset")
    assert schema.source_name == "my_dataset"


def test_source_name_default():
    provider = InMemoryProvider()
    schema = provider.extract(make_polars_df())
    assert schema.source_name == "memory"


def test_value_count_correct():
    provider = InMemoryProvider()
    schema = provider.extract(make_polars_df())
    id_field = next(f for f in schema.fields if f.name == "id")
    assert id_field.value_count == 4


def test_sample_values_present():
    provider = InMemoryProvider()
    schema = provider.extract(make_polars_df())
    id_field = next(f for f in schema.fields if f.name == "id")
    assert len(id_field.sample_values) > 0
