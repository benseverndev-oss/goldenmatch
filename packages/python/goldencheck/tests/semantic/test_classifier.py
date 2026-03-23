import polars as pl
from goldencheck.semantic.classifier import classify_columns

def test_name_heuristic_email():
    df = pl.DataFrame({"customer_email": ["a@b.com", "c@d.com"]})
    result = classify_columns(df)
    assert result["customer_email"].type_name == "email"
    assert result["customer_email"].source == "name"

def test_name_heuristic_id():
    df = pl.DataFrame({"order_id": [1, 2, 3]})
    result = classify_columns(df)
    assert result["order_id"].type_name == "identifier"

def test_prefix_match_boolean():
    df = pl.DataFrame({"is_active": [True, False]})
    result = classify_columns(df)
    assert result["is_active"].type_name == "boolean"

def test_no_match_returns_none():
    # Use repeated medium-length all-lowercase values to avoid matching any value signal:
    # - low uniqueness (not identifier), avg len ~7 (not short_strings/geo, not address/free_text)
    # - lowercase only (not mixed_case/person_name), only 2 unique (not code_enum >20)
    # - but max_unique=3 for boolean, so need > 3 unique values; max_unique=20 for code_enum
    # Actually: 2 unique values triggers boolean (max_unique: 3). Use > 20 unique but low pct.
    values = [f"abcdefg{i:03d}" for i in range(25)] * 4  # 25 unique, 100 rows, 25% unique
    df = pl.DataFrame({"xyz_abc": values})
    result = classify_columns(df)
    assert result["xyz_abc"].type_name is None
    assert result["xyz_abc"].source == "none"

def test_value_based_fallback():
    # High uniqueness column with no name hint → identifier
    df = pl.DataFrame({"col_7": list(range(1000))})
    result = classify_columns(df)
    assert result["col_7"].type_name == "identifier"
    assert result["col_7"].source == "value"

def test_free_text_by_name():
    df = pl.DataFrame({"notes": ["hello world", None, "some text"]})
    result = classify_columns(df)
    assert result["notes"].type_name == "free_text"
