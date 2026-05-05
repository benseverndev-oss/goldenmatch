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


def test_preview_validation_error(client):
    bad = {"rules": {"threshold": 1.5, "matchkeys": []}, "sample": {"n": 10, "seed": 1}}
    resp = client.post("/api/v1/preview", json=bad)
    assert resp.status_code == 422
