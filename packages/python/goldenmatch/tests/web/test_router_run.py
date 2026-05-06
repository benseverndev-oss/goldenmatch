"""POST /api/v1/run — full-data execution that writes a saved run to disk.

Distinct from /preview which is sampled + in-memory. The "save run to
project" UX writes lineage.json + clusters.csv to project_root so the
new run shows up in the next GET /api/v1/project.
"""
from __future__ import annotations


def test_run_with_current_rules_writes_files_and_appears_in_project(
    client, sample_project,
):
    # Adopt rules first so /run has something to use.
    client.put(
        "/api/v1/rules",
        json={
            "threshold": 0.5,
            "matchkeys": [
                {
                    "column": "name",
                    "scorer": "jaro_winkler",
                    "weight": 1.0,
                    "transforms": ["lowercase"],
                }
            ],
        },
    )
    resp = client.post("/api/v1/run", json={})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["run_name"]
    assert body["total_pairs"] >= 1, "run should have found at least one pair"

    # Files landed on disk under the project root.
    lineage = sample_project / f"{body['run_name']}_lineage.json"
    clusters = sample_project / f"{body['run_name']}_clusters.csv"
    assert lineage.exists()
    assert clusters.exists()

    # And /project surfaces the new run.
    project = client.get("/api/v1/project").json()
    names = [r["run_name"] for r in project["runs"]]
    assert body["run_name"] in names


def test_run_400_when_no_rules_and_no_autoconfig(tmp_path):
    """If rules aren't in state and auto_config=false, the route must 400
    with a message that points at the workbench rather than running with
    nothing."""
    from fastapi.testclient import TestClient
    from goldenmatch.web.app import create_app
    from goldenmatch.web.state import AppState

    # Empty project — no goldenmatch.yml so /rules can't lazy-seed.
    bare = TestClient(create_app(AppState.from_project_dir(tmp_path)))
    resp = bare.post("/api/v1/run", json={})
    assert resp.status_code == 400
    assert "rules" in resp.json()["detail"].lower()


def test_run_autoconfig_requires_no_rules(client):
    """auto_config=true bypasses state.rules and uses the engine's profiler."""
    resp = client.post("/api/v1/run", json={"auto_config": True})
    assert resp.status_code == 200, resp.text
    assert resp.json()["auto_config"] is True


def test_run_llm_boost_400_without_api_key(client, monkeypatch):
    """llm_boost requested + no API key in env → 400, not silent
    fall-through. The user explicitly opted in."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    resp = client.post(
        "/api/v1/run",
        json={"auto_config": True, "llm_boost": True},
    )
    assert resp.status_code == 400
    assert "OPENAI_API_KEY" in resp.json()["detail"]
