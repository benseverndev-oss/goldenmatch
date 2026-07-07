from __future__ import annotations

import io
import json

import goldenmatch.web.routers.documents as docrouter
from fastapi.testclient import TestClient
from goldenmatch.documents.extractor import FakeExtractor
from goldenmatch.documents.types import ExtractedRow, ExtractResult, Field, TargetSchema
from goldenmatch.web.app import create_app
from goldenmatch.web.state import AppState
from PIL import Image

SCHEMA = TargetSchema([Field("full_name"), Field("email")])


def _client(tmp_path):
    state = AppState(project_root=tmp_path, config_path=None,
                     labels_path=tmp_path / "labels.jsonl")
    return TestClient(create_app(state))


def _png_bytes():
    buf = io.BytesIO(); Image.new("RGB", (20, 20), "white").save(buf, format="PNG")
    return buf.getvalue()


def test_document_routes_registered(tmp_path):
    client = _client(tmp_path)
    paths = {r.path for r in client.app.routes}
    assert "/api/v1/documents/suggest-schema" in paths
    assert "/api/v1/documents/ingest" in paths


def test_suggest_schema_returns_schema(tmp_path, monkeypatch):
    monkeypatch.setattr(docrouter, "suggest_schema_from_file",
                        lambda path, **k: TargetSchema([Field("full_name"), Field("email", kind="email")]))
    client = _client(tmp_path)
    resp = client.post("/api/v1/documents/suggest-schema",
                       files={"file": ("card.png", _png_bytes(), "image/png")})
    assert resp.status_code == 200, resp.text
    assert resp.json()["schema"]["fields"][0]["name"] == "full_name"


def test_ingest_returns_records_and_report(tmp_path, monkeypatch):
    row = ExtractedRow.from_partial({"full_name": "Ada", "email": "ada@x.io"}, {},
                                    SCHEMA, source_file="", source_page=0)
    monkeypatch.setattr(docrouter, "resolve_extractor",
                        lambda b, m: FakeExtractor([ExtractResult(rows=[row])]))
    client = _client(tmp_path)
    resp = client.post(
        "/api/v1/documents/ingest",
        files=[("files", ("a.png", _png_bytes(), "image/png"))],
        data={"schema": json.dumps({"fields": [{"name": "full_name"}, {"name": "email"}]})},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["report"]["n_rows"] == 1 and body["report"]["n_files"] == 1
    rec = body["records"][0]
    assert rec["full_name"] == "Ada" and rec["email"] == "ada@x.io"
    # sidecar columns present for the dedupe_df exclude_columns handoff
    assert "_source_file" in rec and "_source_page" in rec and "_extract_confidence" in rec
