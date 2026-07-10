import polars as pl
from goldenmatch.documents.assemble import assemble
from goldenmatch.documents.types import (
    ExtractedRow,
    ExtractResult,
    Field,
    TargetSchema,
)

SCHEMA = TargetSchema([Field("full_name"), Field("email")])


def _row(vals, conf, f="a.pdf", pg=0):
    return ExtractedRow.from_partial(vals, conf, SCHEMA, source_file=f, source_page=pg)


def test_assemble_builds_frame_with_schema_and_sidecar_columns():
    results = [
        ExtractResult(rows=[_row({"full_name": "Ada", "email": "ada@x.io"},
                                 {"full_name": 0.9, "email": 0.8})]),
        ExtractResult(rows=[  # a 2-row "table" doc
            _row({"full_name": "Bo", "email": "bo@x.io"}, {"full_name": 0.7, "email": 0.7}, f="t.pdf", pg=1),
            _row({"full_name": "Cy", "email": None}, {"full_name": 0.6, "email": 0.0}, f="t.pdf", pg=1),
        ]),
    ]
    df, report = assemble(results, SCHEMA, drop_empty=True)
    assert df.columns == ["full_name", "email", "_source_file", "_source_page", "_extract_confidence"]
    assert df.height == 3
    assert df["_source_file"].to_list() == ["a.pdf", "t.pdf", "t.pdf"]
    # row_confidence is the min over fields
    assert df.filter(pl.col("full_name") == "Cy")["_extract_confidence"][0] == 0.0
    assert report.n_files == 2 and report.n_rows == 3 and report.errors == []


def test_assemble_records_errors_and_continues():
    results = [ExtractResult(rows=[], error="bad json"),
               ExtractResult(rows=[_row({"full_name": "Ada", "email": "a@x.io"},
                                        {"full_name": 0.9, "email": 0.9}, f="ok.png")])]
    df, report = assemble(results, SCHEMA, drop_empty=True, files=["bad.pdf", "ok.png"])
    assert df.height == 1
    assert report.errors == [("bad.pdf", "bad json")]


def test_assemble_drop_empty_removes_all_null_rows():
    results = [ExtractResult(rows=[_row({"full_name": None, "email": None}, {})])]
    df, _ = assemble(results, SCHEMA, drop_empty=True)
    assert df.height == 0


def test_assemble_empty_input_yields_empty_typed_frame():
    df, report = assemble([], SCHEMA, drop_empty=True)
    assert df.columns == ["full_name", "email", "_source_file", "_source_page", "_extract_confidence"]
    assert df.height == 0 and report.n_rows == 0
