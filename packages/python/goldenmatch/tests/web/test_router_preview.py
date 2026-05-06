from __future__ import annotations


def test_preview_runs_and_serves_via_runs(client, sample_project):
    payload = {
        "rules": {
            "threshold": 0.5,
            "matchkeys": [{
                "column": "name", "scorer": "jaro_winkler",
                "weight": 1.0, "transforms": ["lowercase"]
            }]
        },
        "sample": {"n": 10, "seed": 42}
    }
    resp = client.post("/api/v1/preview", json=payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["run_name"].startswith("preview-")

    # served by runs endpoints
    m = client.get(f"/api/v1/runs/{body['run_name']}")
    assert m.status_code == 200


def test_preview_actually_finds_matches(client, sample_project):
    """Regression: the v1 preview built GoldenMatchConfig(matchkey=...) (singular)
    instead of the actual schema field matchkeys= (plural), and omitted blocking.
    Pydantic silently dropped the bad kwarg so the engine ran with no matchkeys
    and produced all-singletons. Tests only checked HTTP shape so it shipped.

    The Sony Silver / Sony Black pair in the fixture clears jaro_winkler ≥ 0.5
    cleanly. If preview returns no pairs in this run's lineage, the engine
    integration is broken even though the route returns 200.
    """
    payload = {
        "rules": {
            "threshold": 0.5,
            "matchkeys": [{
                "column": "name", "scorer": "jaro_winkler",
                "weight": 1.0, "transforms": ["lowercase"],
            }],
        },
        "sample": {"n": 10, "seed": 42},
    }
    resp = client.post("/api/v1/preview", json=payload)
    assert resp.status_code == 200, resp.text
    name = resp.json()["run_name"]

    manifest = client.get(f"/api/v1/runs/{name}").json()
    assert manifest["total_pairs"] >= 1, (
        f"preview produced no pairs — engine integration broken. manifest={manifest}"
    )


def test_preview_validation_error(client):
    bad = {"rules": {"threshold": 1.5, "matchkeys": []}, "sample": {"n": 10, "seed": 1}}
    resp = client.post("/api/v1/preview", json=bad)
    assert resp.status_code == 422


def test_preview_missing_data_csv_returns_400(tmp_path):
    """Empty project (no data.csv) should 400 with a useful message, not 500."""
    from fastapi.testclient import TestClient
    from goldenmatch.web.app import create_app
    from goldenmatch.web.state import AppState

    bare = TestClient(create_app(AppState.from_project_dir(tmp_path)))
    payload = {
        "rules": {"threshold": 0.85, "matchkeys": [
            {"column": "name", "scorer": "jaro_winkler", "weight": 1.0, "transforms": []}
        ]},
        "sample": {"n": 10, "seed": 0},
    }
    resp = bare.post("/api/v1/preview", json=payload)
    assert resp.status_code == 400
    assert "data.csv" in resp.json()["detail"]


def test_preview_unknown_column_returns_400(client):
    """Matchkey referencing a column absent from data.csv must surface as 400."""
    payload = {
        "rules": {"threshold": 0.5, "matchkeys": [
            {"column": "no_such_column", "scorer": "exact",
             "weight": 1.0, "transforms": []}
        ]},
        "sample": {"n": 10, "seed": 0},
    }
    resp = client.post("/api/v1/preview", json=payload)
    assert resp.status_code == 400
    assert "no_such_column" in resp.json()["detail"] or "preview failed" in resp.json()["detail"]


def test_preview_rejects_embedding_scorer(client):
    """Embedding scorers need model bootstrap; reject upfront with a clear 400."""
    payload = {
        "rules": {"threshold": 0.5, "matchkeys": [
            {"column": "name", "scorer": "embedding",
             "weight": 1.0, "transforms": []}
        ]},
        "sample": {"n": 10, "seed": 0},
    }
    resp = client.post("/api/v1/preview", json=payload)
    assert resp.status_code == 400
    assert "embedding" in resp.json()["detail"]
