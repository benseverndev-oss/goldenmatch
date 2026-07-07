from __future__ import annotations

import io

import goldenmatch.web.routers.documents as docrouter
from fastapi.testclient import TestClient
from goldenmatch.documents.types import Field, TargetSchema
from goldenmatch.web.app import create_app
from goldenmatch.web.state import AppState
from PIL import Image


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
