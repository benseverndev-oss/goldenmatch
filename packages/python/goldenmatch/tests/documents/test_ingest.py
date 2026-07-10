import pytest
from goldenmatch.documents import DOC_SIDECARS, ingest_documents
from goldenmatch.documents.extractor import (
    FakeClassifier,
    FakeExtractor,
    FakeFallbackExtractor,
    FakeTemplateExtractor,
)
from goldenmatch.documents.templates import get_template
from goldenmatch.documents.types import (
    ClassifyResult,
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


# ---- Task 6: auto-classify flow (all offline via injected fakes) --------------

class _RaisingClassifier:
    """Classify call goes out but the response fails to parse -> raises. Exposes
    `.calls` like the fakes so we can assert the call was issued."""

    def __init__(self):
        self.calls = 0

    def classify(self, pages):
        self.calls += 1
        raise ValueError("classify blew up")


def test_auto_classify_high_confidence_invoice(tmp_path):
    a = tmp_path / "inv.png"; _img(a)
    header = _erow(INV.header, {"invoice_number": "INV-1", "total_amount": "500"})
    items = [_erow(INV.line_items, {"description": "Widget", "quantity": "3"}),
             _erow(INV.line_items, {"description": "Bolt", "quantity": "4"})]
    cls = FakeClassifier([ClassifyResult("invoice", 0.9)])
    te = FakeTemplateExtractor([StructuredResult(header=header, line_items=items, error=None)])
    fb = FakeFallbackExtractor([])  # never called on the hit path
    df, report = ingest_documents([a], classifier=cls, template_extractor=te,
                                  fallback_extractor=fb, return_report=True)
    assert df.height == 1
    doc_id = df["_doc_id"][0]
    assert df["_doctype"][0] == "invoice"
    assert report.vlm_calls == 2
    assert report.doctypes[doc_id] == "invoice"
    assert report.classify_confidence[doc_id] == 0.9
    assert report.line_items is not None and report.line_items.height == 2
    assert cls.calls == 1 and te.calls == 1 and fb.calls == 0


def test_auto_classify_low_confidence_falls_back_generic(tmp_path):
    a = tmp_path / "doc.png"; _img(a)
    GEN = TargetSchema([Field("full_name"), Field("email")])
    flat = ExtractResult(rows=_rows(GEN, [({"full_name": "Ada", "email": "a@x.io"}, {})], "doc"))
    cls = FakeClassifier([ClassifyResult("invoice", 0.3)])   # below threshold 0.6
    te = FakeTemplateExtractor([])   # must NOT be touched
    fb = FakeFallbackExtractor([flat])
    df, report = ingest_documents([a], classifier=cls, template_extractor=te,
                                  fallback_extractor=fb, return_report=True)
    assert df.height == 1
    doc_id = df["_doc_id"][0]
    assert df["_doctype"][0] == "generic"
    assert report.vlm_calls == 3
    assert report.doctypes[doc_id] == "generic"
    assert report.classify_confidence[doc_id] == 0.3
    assert cls.calls == 1 and te.calls == 0 and fb.calls == 1


def test_auto_classify_generic_doctype_falls_back(tmp_path):
    a = tmp_path / "doc.png"; _img(a)
    GEN = TargetSchema([Field("full_name")])
    flat = ExtractResult(rows=_rows(GEN, [({"full_name": "Bo"}, {})], "doc"))
    # "generic" is high confidence but not in list_templates() -> fallback.
    cls = FakeClassifier([ClassifyResult("generic", 0.95)])
    te = FakeTemplateExtractor([])
    fb = FakeFallbackExtractor([flat])
    df, report = ingest_documents([a], classifier=cls, template_extractor=te,
                                  fallback_extractor=fb, return_report=True)
    doc_id = df["_doc_id"][0]
    assert df["_doctype"][0] == "generic"
    assert report.vlm_calls == 3
    assert report.classify_confidence[doc_id] == 0.95
    assert te.calls == 0 and fb.calls == 1


def test_auto_classify_template_override_skips_classifier(tmp_path):
    a = tmp_path / "inv.png"; _img(a)
    header = _erow(INV.header, {"invoice_number": "INV-2"})
    te = FakeTemplateExtractor([StructuredResult(header=header, line_items=[], error=None)])
    cls = FakeClassifier([ClassifyResult("invoice", 0.9)])   # injected but unused
    df, report = ingest_documents([a], template="invoice", template_extractor=te,
                                  classifier=cls, return_report=True)
    assert cls.calls == 0
    assert report.vlm_calls == 1
    assert report.doctypes[df["_doc_id"][0]] == "invoice"


def test_auto_classify_classify_raises_falls_back(tmp_path):
    a = tmp_path / "doc.png"; _img(a)
    GEN = TargetSchema([Field("full_name")])
    flat = ExtractResult(rows=_rows(GEN, [({"full_name": "Cy"}, {})], "doc"))
    cls = _RaisingClassifier()
    te = FakeTemplateExtractor([])
    fb = FakeFallbackExtractor([flat])
    df, report = ingest_documents([a], classifier=cls, template_extractor=te,
                                  fallback_extractor=fb, return_report=True)
    doc_id = df["_doc_id"][0]
    assert df["_doctype"][0] == "generic"
    assert report.vlm_calls == 3   # classify (issued, raised) + suggest + extract
    assert report.classify_confidence[doc_id] == 0.0
    assert cls.calls == 1 and te.calls == 0 and fb.calls == 1
    # a RAISED classify is not a genuine 0.0 classification -- it must leave a trace
    # in report.errors (naming the file), not silently masquerade as low-confidence.
    assert any(str(a) == fname and "classify failed" in msg
               for fname, msg in report.errors)
    # the warning goes to errors, NOT the two report maps -> key-sets stay aligned.
    assert set(report.classify_confidence) == set(report.doctypes)


def test_auto_classify_warning_surfaced_when_fallback_empty(tmp_path):
    a = tmp_path / "doc.png"; _img(a)
    empty = ExtractResult(rows=[], error=None)  # fallback produced no rows
    cls = _RaisingClassifier()
    te = FakeTemplateExtractor([])
    fb = FakeFallbackExtractor([empty])
    df, report = ingest_documents([a], classifier=cls, template_extractor=te,
                                  fallback_extractor=fb, return_report=True)
    assert df.height == 0
    # no header row, but the classify failure must still name the file in errors.
    assert any(str(a) == fname and "classify failed" in msg
               for fname, msg in report.errors)


def test_auto_classify_false_without_schema_or_template_raises(tmp_path):
    a = tmp_path / "a.png"; _img(a)
    with pytest.raises(ValueError, match="auto_classify=False"):
        ingest_documents([a], auto_classify=False)


def test_auto_classify_true_default_still_auto_classifies(tmp_path):
    a = tmp_path / "inv.png"; _img(a)
    header = _erow(INV.header, {"invoice_number": "INV-5"})
    cls = FakeClassifier([ClassifyResult("invoice", 0.9)])
    te = FakeTemplateExtractor([StructuredResult(header=header, line_items=[], error=None)])
    fb = FakeFallbackExtractor([])
    df, report = ingest_documents([a], classifier=cls, template_extractor=te,
                                  fallback_extractor=fb, return_report=True)
    assert cls.calls == 1 and report.doctypes[df["_doc_id"][0]] == "invoice"


def test_flat_path_needs_no_api_key(tmp_path, monkeypatch):
    """The flat schema= path with an injected extractor must never reach
    resolve_structured/resolve_api_key -- succeeds with no key in the env."""
    monkeypatch.delenv("OPENAI_API_KEY_PERSONAL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    a = tmp_path / "a.png"; _img(a)
    GEN = TargetSchema([Field("full_name")])
    fake = FakeExtractor([ExtractResult(rows=_rows(GEN, [({"full_name": "Ada"}, {})], "a"))])
    df = ingest_documents([a], GEN, extractor=fake)
    assert df.height == 1


def test_auto_classify_structured_error_recorded_batch_continues(tmp_path):
    good, bad = tmp_path / "good.png", tmp_path / "bad.png"
    _img(good); _img(bad)
    header = _erow(INV.header, {"invoice_number": "INV-3"})
    cls = FakeClassifier([ClassifyResult("invoice", 0.9), ClassifyResult("invoice", 0.9)])
    te = FakeTemplateExtractor([
        StructuredResult(header=header, line_items=[], error=None),
        StructuredResult(header=None, line_items=[], error="parse blew up"),
    ])
    fb = FakeFallbackExtractor([])
    df, report = ingest_documents([good, bad], classifier=cls, template_extractor=te,
                                  fallback_extractor=fb, return_report=True)
    assert df.height == 1   # only the good doc produced a row
    assert any("parse blew up" == msg for _, msg in report.errors)
    assert report.vlm_calls == 4   # both docs classify+extract (2 each), even the errored one


def test_auto_classify_does_not_resolve_when_all_injected_no_key(tmp_path, monkeypatch):
    """The auto path must not touch resolve_structured (=> no api key needed) when
    all three collaborators are injected."""
    monkeypatch.delenv("OPENAI_API_KEY_PERSONAL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    a = tmp_path / "inv.png"; _img(a)
    header = _erow(INV.header, {"invoice_number": "INV-4"})
    cls = FakeClassifier([ClassifyResult("invoice", 0.9)])
    te = FakeTemplateExtractor([StructuredResult(header=header, line_items=[], error=None)])
    fb = FakeFallbackExtractor([])
    df = ingest_documents([a], classifier=cls, template_extractor=te, fallback_extractor=fb)
    assert df.height == 1
