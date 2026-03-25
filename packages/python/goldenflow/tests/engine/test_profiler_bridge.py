import polars as pl

from goldenflow.engine.profiler_bridge import ColumnProfile, DatasetProfile, profile_dataframe


def test_profile_dataframe_returns_dataset_profile():
    df = pl.DataFrame({
        "name": ["John Smith", "Jane Doe"],
        "email": ["john@test.com", "jane@test.com"],
        "phone": ["(555) 123-4567", "555-987-6543"],
        "age": [30, 25],
    })
    profile = profile_dataframe(df)
    assert isinstance(profile, DatasetProfile)
    assert profile.row_count == 2
    assert profile.column_count == 4
    assert len(profile.columns) == 4


def test_column_profile_fields():
    df = pl.DataFrame({"email": ["john@test.com", "jane@test.com", None]})
    profile = profile_dataframe(df)
    col = profile.columns[0]
    assert isinstance(col, ColumnProfile)
    assert col.name == "email"
    assert col.null_count == 1
    assert col.unique_count == 2


def test_inferred_types():
    df = pl.DataFrame({
        "email": ["john@test.com", "jane@test.com"],
        "phone": ["(555) 123-4567", "+15559876543"],
        "date": ["2024-01-01", "03/15/2024"],
        "number": [1, 2],
    })
    profile = profile_dataframe(df)
    types = {c.name: c.inferred_type for c in profile.columns}
    assert types["email"] == "email"
    assert types["phone"] == "phone"
    assert types["date"] == "date"
    assert types["number"] == "numeric"
