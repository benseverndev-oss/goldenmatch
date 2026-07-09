import pytest
from goldenmatch.documents import DOC_SIDECARS, ingest_documents
from goldenmatch.documents.extractor import FakeExtractor, FakeTemplateExtractor
from goldenmatch.documents.templates import get_template
from goldenmatch.documents.types import (
    ExtractedRow,
    ExtractResult,
    Field,
    StructuredResult,
    TargetSchema,
)
from PIL import Image

SCHEMA = TargetSchema([Field("full_name"), Field("email")])
INV = get_template("invoice")


def _img(path):
    Image.new("RGB", (60, 40), "white").save(path)


def _rows(schema, pairs, f, pg=0):
    return [ExtractedRow.from_partial(v, c, schema, source_file="", source_page=pg)
            for (v, c) in pairs]


def test_ingest_stamps_filenames_and_returns_frame(tmp_path):
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    _img(a); _img(b)
    fake = FakeExtractor([
        ExtractResult(rows=_rows(SCHEMA, [({"full_name": "Ada", "email": "ada@x.io"},
                                           {"full_name": 0.9, "email": 0.9})], "a")),
        ExtractResult(rows=_rows(SCHEMA, [({"full_name": "Bo", "email": "bo@x.io"},
                                           {"full_name": 0.8, "email": 0.8})], "b")),
    ])
    df = ingest_documents([a, b], SCHEMA, extractor=fake)
    assert df.height == 2
    assert set(df["_source_file"].to_list()) == {str(a), str(b)}
    assert df.columns[:2] == ["full_name", "email"]


def test_return_report_returns_tuple(tmp_path):
    a = tmp_path / "a.png"; _img(a)
    fake = FakeExtractor([ExtractResult(rows=_rows(SCHEMA,
                          [({"full_name": "Ada", "email": "a@x.io"}, {})], "a"))])
    df, report = ingest_documents([a], SCHEMA, extractor=fake, return_report=True)
    assert df.height == 1 and report.n_files == 1 and report.n_rows == 1


def test_missing_key_for_vlm_backend_fails_fast(tmp_path, monkeypatch):
    a = tmp_path / "a.png"; _img(a)
    monkeypatch.delenv("OPENAI_API_KEY_PERSONAL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="API key"):
        ingest_documents([a], SCHEMA, backend="vlm")


def test_unknown_backend_fails_fast(tmp_path):
    a = tmp_path / "a.png"; _img(a)
    with pytest.raises(ValueError, match="unsupported backend"):
        ingest_documents([a], SCHEMA, backend="nope")


def test_bad_file_recorded_and_good_file_survives(tmp_path):
    bad = tmp_path / "bad.txt"
    bad.write_text("not an image")
    good = tmp_path / "good.png"; _img(good)
    # load_pages() raises for bad.txt before the extractor is ever called, so only
    # good.png reaches the extractor -> exactly one scripted ExtractResult.
    fake = FakeExtractor([ExtractResult(rows=_rows(SCHEMA,
                          [({"full_name": "Ada", "email": "a@x.io"}, {})], "good"))])
    df, report = ingest_documents([bad, good], SCHEMA, extractor=fake, return_report=True)
    assert df.height == 1
    assert df["full_name"].to_list() == ["Ada"]
    assert any(str(bad) == fname for fname, _ in report.errors)


def _erow(schema, vals):
    return ExtractedRow.from_partial(vals, {}, schema, source_file="", source_page=0)


def test_ingest_template_pinned_structured_e2e(tmp_path):
    a = tmp_path / "inv.png"; _img(a)
    header = _erow(INV.header, {"invoice_number": "INV-9", "total_amount": "200"})
    items = [_erow(INV.line_items, {"description": "Widget", "quantity": "2"}),
             _erow(INV.line_items, {"description": "Gadget", "quantity": "1"})]
    te = FakeTemplateExtractor([StructuredResult(header=header, line_items=items, error=None)])
    df, report = ingest_documents([a], template="invoice", template_extractor=te,
                                  return_report=True)
    assert df.height == 1
    doc_id = df["_doc_id"][0]
    assert df["_doctype"][0] == "invoice"
    assert "invoice_number" in df.columns
    # DOC_SIDECARS present so the header frame feeds
    # dedupe_df(df, exclude_columns=DOC_SIDECARS) cleanly; match cols survive.
    assert set(DOC_SIDECARS) <= set(df.columns)
    assert report.vlm_calls == 1
    assert report.classify_confidence[doc_id] == 1.0
    assert report.doctypes[doc_id] == "invoice"
    assert report.line_items is not None and report.line_items.height == 2
    assert report.line_items["_doc_id"].to_list() == [doc_id, doc_id]
    assert te.calls == 1


def test_ingest_schema_and_template_both_set_raises(tmp_path):
    a = tmp_path / "a.png"; _img(a)
    with pytest.raises(ValueError, match="schema.*template|template.*schema"):
        ingest_documents([a], SCHEMA, template="invoice")


def test_ingest_auto_classify_not_yet_implemented(tmp_path):
    a = tmp_path / "a.png"; _img(a)
    with pytest.raises(NotImplementedError, match="Task 6|auto-classify"):
        ingest_documents([a])
