import json

import goldenmatch.cli.ingest_docs as ci
from goldenmatch.documents.extractor import FakeExtractor
from goldenmatch.documents.types import ExtractedRow, ExtractResult, Field, TargetSchema
from typer.testing import CliRunner

runner = CliRunner()
SCHEMA = TargetSchema([Field("full_name"), Field("email")])


def _schema_file(tmp_path):
    p = tmp_path / "schema.json"
    p.write_text(json.dumps({"fields": [{"name": "full_name"}, {"name": "email"}]}))
    return p


def test_run_writes_records_csv(tmp_path, monkeypatch):
    from PIL import Image
    img = tmp_path / "a.png"; Image.new("RGB", (10, 10), "white").save(img)
    row = ExtractedRow.from_partial({"full_name": "Ada", "email": "a@x.io"}, {}, SCHEMA,
                                    source_file="", source_page=0)
    monkeypatch.setattr(ci, "resolve_extractor", lambda b, m: FakeExtractor([ExtractResult(rows=[row])]))
    out = tmp_path / "recs.csv"
    r = runner.invoke(ci.ingest_docs_app, ["run", str(img), "--schema", str(_schema_file(tmp_path)),
                                           "--out", str(out)])
    assert r.exit_code == 0, r.output
    assert out.exists() and "Ada" in out.read_text()


def test_suggest_schema_writes_file(tmp_path, monkeypatch):
    from PIL import Image
    img = tmp_path / "s.png"; Image.new("RGB", (10, 10), "white").save(img)
    monkeypatch.setattr(ci, "suggest_schema_from_file", lambda path, **k: SCHEMA)
    out = tmp_path / "schema.json"
    r = runner.invoke(ci.ingest_docs_app, ["suggest-schema", str(img), "--out", str(out)])
    assert r.exit_code == 0, r.output
    assert json.loads(out.read_text())["fields"][0]["name"] == "full_name"
