import pytest
from goldenmatch.documents import ingest_documents
from goldenmatch.documents.extractor import FakeExtractor
from goldenmatch.documents.types import (
    ExtractedRow,
    ExtractResult,
    Field,
    TargetSchema,
)
from PIL import Image

SCHEMA = TargetSchema([Field("full_name"), Field("email")])


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
