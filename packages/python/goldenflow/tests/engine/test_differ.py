import polars as pl
from goldenflow.engine.differ import diff_dataframes


def test_diff_identical():
    df = pl.DataFrame({"a": [1, 2], "b": ["x", "y"]})
    result = diff_dataframes(df, df)
    assert result.total_changes == 0


def test_diff_value_changes():
    before = pl.DataFrame({"a": ["  hello  ", "world"], "b": [1, 2]})
    after = pl.DataFrame({"a": ["hello", "world"], "b": [1, 2]})
    result = diff_dataframes(before, after)
    assert result.total_changes == 1
    assert "a" in result.changed_columns


def test_diff_column_added():
    before = pl.DataFrame({"a": [1]})
    after = pl.DataFrame({"a": [1], "b": [2]})
    result = diff_dataframes(before, after)
    assert "b" in result.added_columns


def test_diff_column_removed():
    before = pl.DataFrame({"a": [1], "b": [2]})
    after = pl.DataFrame({"a": [1]})
    result = diff_dataframes(before, after)
    assert "b" in result.removed_columns
