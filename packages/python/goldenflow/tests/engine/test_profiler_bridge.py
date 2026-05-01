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


def test_goldencheck_semantic_type_mapping():
    """GoldenCheck semantic types should map to GoldenFlow's type system."""
    from goldenflow.engine.profiler_bridge import _map_goldencheck_semantic_type

    # Direct mappings
    assert _map_goldencheck_semantic_type("person_name") == "name"
    assert _map_goldencheck_semantic_type("email") == "email"
    assert _map_goldencheck_semantic_type("phone") == "phone"
    assert _map_goldencheck_semantic_type("address") == "address"
    assert _map_goldencheck_semantic_type("datetime") == "date"
    assert _map_goldencheck_semantic_type("boolean") == "boolean"
    assert _map_goldencheck_semantic_type("currency") == "numeric"
    assert _map_goldencheck_semantic_type("identifier") == "string"
    assert _map_goldencheck_semantic_type("free_text") == "string"

    # code_enum should be string (categorical handling is by cardinality, not type)
    assert _map_goldencheck_semantic_type("code_enum") == "string"

    # geo types
    assert _map_goldencheck_semantic_type("geo") == "string"

    # Unknown falls through
    assert _map_goldencheck_semantic_type("unknown_type") == "string"


def test_column_name_override_still_works_with_semantic_types():
    """Column name heuristics should still apply when GoldenCheck gives generic types."""
    from goldenflow.engine.profiler_bridge import _override_type_by_column_name

    # Name heuristic overrides generic string
    assert _override_type_by_column_name("first_name", "string") == "name"
    assert _override_type_by_column_name("zip_code", "string") == "zip"

    # But doesn't override already-specific types
    assert _override_type_by_column_name("email_address", "email") == "email"
