from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from goldenmatch.web.app import create_app
from goldenmatch.web.state import AppState


def test_app_serves_root(tmp_path: Path) -> None:
    state = AppState(project_root=tmp_path, config_path=None, labels_path=tmp_path / "labels.jsonl")
    app = create_app(state)
    client = TestClient(app)
    resp = client.get("/api/v1/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
