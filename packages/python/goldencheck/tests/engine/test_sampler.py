import polars as pl
from goldencheck.engine.sampler import maybe_sample

def test_no_sample_small_df():
    df = pl.DataFrame({"a": range(100)})
    result = maybe_sample(df, max_rows=1000)
    assert len(result) == 100

def test_sample_large_df():
    df = pl.DataFrame({"a": range(10000)})
    result = maybe_sample(df, max_rows=1000)
    assert len(result) == 1000

def test_sample_preserves_columns():
    df = pl.DataFrame({"a": range(5000), "b": range(5000)})
    result = maybe_sample(df, max_rows=100)
    assert result.columns == ["a", "b"]
