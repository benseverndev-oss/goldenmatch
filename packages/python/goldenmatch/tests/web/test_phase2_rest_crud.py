"""Tests for Phase 2 of v1.19.0 surface-sync roadmap.

Spec: docs/superpowers/specs/2026-05-22-phase-2-rest-api-crud-design.md

Covers:
- 2.1 POST /api/v1/memory/corrections (pair + field shapes; validation)
- 2.2 GET /api/v1/plugins (22 builtins + category filter + user override)
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    """Build an isolated TestClient with a fresh memory DB per test."""
    from goldenmatch.web.app import create_app
    from goldenmatch.web.state import AppState

    db_path = tmp_path / "memory.db"
    # Re-route default memory DB path through env so get_memory() picks it up.
    monkeypatch.setenv("GOLDENMATCH_MEMORY_PATH", str(db_path))
    # CWD-relative path for any code using it.
    monkeypatch.chdir(tmp_path)

    state = AppState.from_project_dir(tmp_path)
    app = create_app(state)
    return TestClient(app)


# ---------------------------------------------------------------------------
# 2.1 POST /api/v1/memory/corrections
# ---------------------------------------------------------------------------


def test_post_correction_pair_level(client: TestClient):
    payload = {
        "decision": "approve",
        "dataset": "test_dataset",
        "id_a": 42,
        "id_b": 99,
        "reason": "verified by analyst",
    }
    resp = client.post("/api/v1/memory/corrections", json=payload)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["decision"] == "approve"
    assert body["id_a"] == 42
    assert body["id_b"] == 99
    assert body["source"] == "rest"
    assert body["trust"] == 0.8


def test_post_correction_field_level(client: TestClient):
    payload = {
        "decision": "field_correct",
        "dataset": "test_dataset",
        "cluster_id": 42,
        "field_name": "address1",
        "original_value": "1 Elm St",
        "corrected_value": "1 Elm Street, Apt 4B",
    }
    resp = client.post("/api/v1/memory/corrections", json=payload)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["decision"] == "field_correct"
    assert body["field_name"] == "address1"
    assert body["corrected_value"] == "1 Elm Street, Apt 4B"
    assert body["original_value"] == "1 Elm St"


def test_post_correction_pair_missing_id_b(client: TestClient):
    resp = client.post(
        "/api/v1/memory/corrections",
        json={"decision": "approve", "dataset": "test", "id_a": 42},
    )
    assert resp.status_code == 400
    assert "id_a and id_b" in resp.text


def test_post_correction_field_missing_corrected_value(client: TestClient):
    resp = client.post(
        "/api/v1/memory/corrections",
        json={
            "decision": "field_correct",
            "dataset": "test",
            "cluster_id": 42,
            "field_name": "address1",
        },
    )
    assert resp.status_code == 400
    assert "corrected_value" in resp.text


def test_post_correction_field_missing_field_name(client: TestClient):
    resp = client.post(
        "/api/v1/memory/corrections",
        json={
            "decision": "field_correct",
            "dataset": "test",
            "cluster_id": 42,
            "corrected_value": "X",
        },
    )
    assert resp.status_code == 400
    assert "field_name" in resp.text


def test_post_correction_invalid_decision(client: TestClient):
    resp = client.post(
        "/api/v1/memory/corrections",
        json={"decision": "shrug", "dataset": "test"},
    )
    assert resp.status_code == 400


def test_post_correction_appears_in_list(client: TestClient):
    """Post + GET roundtrip in the same TestClient session."""
    client.post(
        "/api/v1/memory/corrections",
        json={
            "decision": "field_correct",
            "dataset": "test",
            "cluster_id": 42,
            "field_name": "email",
            "corrected_value": "fixed@x.com",
        },
    )
    resp = client.get("/api/v1/memory/corrections", params={"dataset": "test"})
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["field_name"] == "email"
    assert items[0]["corrected_value"] == "fixed@x.com"


# ---------------------------------------------------------------------------
# 2.2 GET /api/v1/plugins
# ---------------------------------------------------------------------------


def test_get_plugins_default_returns_all_categories(client: TestClient):
    from goldenmatch.plugins.registry import PluginRegistry

    PluginRegistry.reset()
    resp = client.get("/api/v1/plugins")
    PluginRegistry.reset()
    assert resp.status_code == 200
    body = resp.json()
    assert "golden_strategy" in body
    # 22 v1.18.2 builtin plugins (+ any test contamination)
    names = {p["name"] for p in body["golden_strategy"]}
    for expected in (
        "numeric_max", "numeric_mean", "email_normalize",
        "phone_digits_only", "system_of_record", "lifecycle_stage",
        "agreement_rate", "count_distinct",
    ):
        assert expected in names, f"{expected} not in /plugins response"


def test_get_plugins_category_filter(client: TestClient):
    from goldenmatch.plugins.registry import PluginRegistry

    PluginRegistry.reset()
    resp = client.get(
        "/api/v1/plugins", params={"category": "golden_strategy"},
    )
    PluginRegistry.reset()
    assert resp.status_code == 200
    assert set(resp.json().keys()) == {"golden_strategy"}


def test_get_plugins_each_entry_has_required_fields(client: TestClient):
    from goldenmatch.plugins.registry import PluginRegistry

    PluginRegistry.reset()
    resp = client.get("/api/v1/plugins")
    PluginRegistry.reset()
    for entry in resp.json()["golden_strategy"]:
        assert "name" in entry
        assert "category" in entry
        assert "source" in entry
        assert "doc" in entry


def test_get_plugins_marks_builtin_source(client: TestClient):
    from goldenmatch.plugins.registry import PluginRegistry

    PluginRegistry.reset()
    resp = client.get("/api/v1/plugins")
    PluginRegistry.reset()
    for entry in resp.json()["golden_strategy"]:
        if entry["name"] in {"numeric_max", "email_normalize"}:
            assert entry["source"] == "builtin"


def test_get_plugins_invalid_category(client: TestClient):
    resp = client.get("/api/v1/plugins", params={"category": "bogus"})
    # FastAPI Query pattern validation returns 422 on regex mismatch.
    assert resp.status_code == 422


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
