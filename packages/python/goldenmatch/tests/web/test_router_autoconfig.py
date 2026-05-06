"""POST /api/v1/autoconfig — auto-build a RulesPayload from data.csv.

The full GoldenMatchConfig is collapsed into the workbench's RulesPayload
(flat matchkey list + threshold) by picking the first weighted matchkey.
Embedding scorers get filtered/demoted so the suggestion can be previewed
immediately without bootstrapping models.
"""
from __future__ import annotations


def test_autoconfig_returns_a_usable_rules_payload(client):
    resp = client.post("/api/v1/autoconfig")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "threshold" in body
    assert isinstance(body["matchkeys"], list)
    # Non-empty: even on the 3-row fixture, autoconfig should pick at least
    # one column and one scorer.
    assert len(body["matchkeys"]) >= 1
    for mk in body["matchkeys"]:
        assert mk["scorer"] not in {"embedding", "record_embedding"}, (
            "embedding scorers must be filtered or demoted by autoconfig"
        )
        assert isinstance(mk["column"], str) and mk["column"]


def test_autoconfig_400_on_missing_data_csv(tmp_path):
    """Empty project → 400 (parallels /preview's contract)."""
    from fastapi.testclient import TestClient
    from goldenmatch.web.app import create_app
    from goldenmatch.web.state import AppState

    bare = TestClient(create_app(AppState.from_project_dir(tmp_path)))
    resp = bare.post("/api/v1/autoconfig")
    assert resp.status_code == 400
    assert "data.csv" in resp.json()["detail"]


def test_autoconfig_writes_to_state_so_get_rules_returns_it(client):
    """After /autoconfig, GET /rules should reflect the suggestion (not the
    yaml seed) — the suggestion is adopted as in-memory rules so the user
    can immediately tweak/preview."""
    suggestion = client.post("/api/v1/autoconfig").json()
    via_get = client.get("/api/v1/rules").json()
    assert via_get["threshold"] == suggestion["threshold"]
    assert len(via_get["matchkeys"]) == len(suggestion["matchkeys"])
