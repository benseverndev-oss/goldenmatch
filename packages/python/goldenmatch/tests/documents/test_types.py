from goldenmatch.documents.types import (
    ExtractedRow,
    ExtractResult,
    Field,
    TargetSchema,
)


def test_target_schema_column_names_preserve_order():
    s = TargetSchema([Field("full_name"), Field("email", kind="email"), Field("city")])
    assert s.column_names() == ["full_name", "email", "city"]


def test_extracted_row_defaults_missing_field_to_none_and_zero_conf():
    s = TargetSchema([Field("full_name"), Field("email")])
    row = ExtractedRow.from_partial(
        {"full_name": "Ada Lovelace"}, {"full_name": 0.9}, s,
        source_file="a.pdf", source_page=0)
    assert row.values == {"full_name": "Ada Lovelace", "email": None}
    assert row.confidence == {"full_name": 0.9, "email": 0.0}
    assert row.row_confidence() == 0.0  # min over fields


def test_extract_result_error_has_no_rows():
    r = ExtractResult(rows=[], error="boom")
    assert r.rows == [] and r.error == "boom"


def test_from_partial_coerces_non_null_values_to_str():
    s = TargetSchema([Field("zip"), Field("phone")])
    row = ExtractedRow.from_partial(
        {"zip": 90210, "phone": None}, {}, s, source_file="a.pdf", source_page=0)
    assert row.values == {"zip": "90210", "phone": None}
