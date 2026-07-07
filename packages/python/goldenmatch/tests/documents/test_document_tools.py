import goldenmatch.mcp.document_tools as dt
from goldenmatch.documents.extractor import FakeExtractor
from goldenmatch.documents.types import ExtractedRow, ExtractResult, Field, TargetSchema

# canonical full form: schema_to_dict always emits name/kind/hint, and schema_from_dict
# accepts this form as input too, so it works as both expected-output and ingest-input.
SCHEMA_JSON = {"fields": [
    {"name": "full_name", "kind": "text", "hint": None},
    {"name": "email", "kind": "email", "hint": None}]}


def test_tool_names_and_list():
    names = {t.name for t in dt.DOCUMENT_TOOLS}
    assert names == {"documents_suggest_schema", "documents_ingest"}
    assert dt.DOCUMENT_TOOL_NAMES == names


def test_suggest_schema_tool(monkeypatch, tmp_path):
    from PIL import Image
    p = tmp_path / "s.png"; Image.new("RGB", (10, 10), "white").save(p)
    schema = TargetSchema([Field("full_name"), Field("email", kind="email")])
    monkeypatch.setattr(dt, "suggest_schema_from_file", lambda path, **k: schema)
    out = dt.handle_document_tool("documents_suggest_schema", {"sample_path": str(p)})
    assert out["schema"] == SCHEMA_JSON


def test_ingest_tool_returns_records_and_report(monkeypatch, tmp_path):
    from PIL import Image
    a = tmp_path / "a.png"; Image.new("RGB", (10, 10), "white").save(a)
    schema = TargetSchema([Field("full_name"), Field("email")])
    row = ExtractedRow.from_partial({"full_name": "Ada", "email": "a@x.io"}, {}, schema,
                                    source_file="", source_page=0)
    monkeypatch.setattr(dt, "resolve_extractor", lambda b, m: FakeExtractor([ExtractResult(rows=[row])]))
    out = dt.handle_document_tool("documents_ingest",
                                  {"paths": [str(a)], "schema": SCHEMA_JSON})
    assert out["report"]["n_rows"] == 1
    assert out["records"][0]["full_name"] == "Ada"
    assert out["records"][0]["email"] == "a@x.io"
