from __future__ import annotations

from fastapi.testclient import TestClient
from goldenmatch.web.app import create_app
from goldenmatch.web.state import AppState


def _client(tmp_path):
    state = AppState(project_root=tmp_path, config_path=None,
                     labels_path=tmp_path / "labels.jsonl")
    return TestClient(create_app(state))


def test_document_routes_registered(tmp_path):
    client = _client(tmp_path)
    paths = {r.path for r in client.app.routes}
    assert "/api/v1/documents/suggest-schema" in paths
    assert "/api/v1/documents/ingest" in paths
