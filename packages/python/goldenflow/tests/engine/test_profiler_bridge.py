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


# --- identifier columns are never profiled as phones -------------------------
# A bare 10-digit id (an NPI, an account or record number) matches the phone
# regex, so zero-config used to rewrite every id to E.164 ("1234567893" ->
# "+11234567893"). The column name is what separates an id from a phone.

_TEN_DIGIT_IDS = ["1234567893", "1659405588", "1902836477", "1487665514", "1003000126"]


def test_identifier_named_digit_column_is_not_phone():
    from goldenflow.engine.profiler_bridge import profile_columns

    cols = {name: list(_TEN_DIGIT_IDS) for name in (
        "npi", "unique_id", "customerId", "account_number", "record-key", "uuid",
    )}
    types = {c.name: c.inferred_type for c in profile_columns(cols).columns}
    assert all(t == "string" for t in types.values()), types


def test_phone_named_digit_column_is_still_phone():
    from goldenflow.engine.profiler_bridge import profile_columns

    cols = {name: list(_TEN_DIGIT_IDS) for name in ("phone_number", "phone_id", "contact")}
    types = {c.name: c.inferred_type for c in profile_columns(cols).columns}
    # A phone token wins over an id token; an uninformative name keeps the
    # value-based inference.
    assert types == {"phone_number": "phone", "phone_id": "phone", "contact": "phone"}


def test_identifier_token_matching_is_whole_word():
    from goldenflow.engine.profiler_bridge import _is_identifier_name

    for name in ("npi", "unique_id", "customerId", "ACCOUNT_NO", "patient-mrn", "SKU"):
        assert _is_identifier_name(name), name
    # Substrings are not tokens: "valid", "monkey", "idea" carry no id token.
    for name in ("valid", "monkey", "idea", "mobile_number", "phone_id", "fax"):
        assert not _is_identifier_name(name), name


def test_zero_config_transform_leaves_identifier_digits_unchanged():
    import goldenflow

    df = pl.DataFrame({"npi": _TEN_DIGIT_IDS, "phone": _TEN_DIGIT_IDS})
    out = goldenflow.transform_df(df).df
    assert out["npi"].to_list() == _TEN_DIGIT_IDS
    assert out["phone"].to_list() == ["+1" + v for v in _TEN_DIGIT_IDS]
