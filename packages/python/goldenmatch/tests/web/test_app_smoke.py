from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from goldenmatch.web.app import create_app
from goldenmatch.web.state import AppState


def test_app_serves_root(tmp_path: Path) -> None:
    state = AppState(project_root=tmp_path, config_path=None, labels_path=tmp_path / "labels.jsonl")
    app = create_app(state)
    client = TestClient(app)
    # Readiness health check (Wave 1.3): degraded without data.csv...
    resp = client.get("/api/v1/healthz")
    assert resp.status_code == 503
    assert resp.json()["status"] == "degraded"
    # ...ready once the project has data.
    (tmp_path / "data.csv").write_text("id\n1\n")
    ready = client.get("/api/v1/healthz")
    assert ready.status_code == 200
    assert ready.json()["status"] == "ok"
