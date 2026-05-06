"""POST /api/v1/sensitivity — parameter sweep against the in-memory rules."""
from __future__ import annotations


def _set_rules(client) -> None:
    """Seed AppState.rules so the router has something to sweep.

    The fixture's goldenmatch.yml mirrors this; we re-PUT it so we exercise
    the same rules → engine translation that the router uses (preview._build_config).
    """
    client.put(
        "/api/v1/rules",
        json={
            "threshold": 0.85,
            "matchkeys": [
                {
                    "column": "name",
                    "scorer": "jaro_winkler",
                    "weight": 1.0,
                    "transforms": ["lowercase", "strip"],
                }
            ],
        },
    )


def test_sensitivity_threshold_sweep_returns_points_and_stability(client):
    _set_rules(client)
    body = client.post(
        "/api/v1/sensitivity",
        json={
            "field": "threshold",
            "start": 0.5,
            "stop": 0.9,
            "step": 0.2,
            "sample_n": 10,
        },
    ).json()

    assert body["field"] == "threshold"
    assert body["sample_n"] == 10
    assert isinstance(body["baseline_value"], (int, float))
    # Three points: 0.5, 0.7, 0.9.
    assert len(body["points"]) == 3
    for p in body["points"]:
        # Each point carries the cluster counts for both runs and a TWI.
        assert {"value", "cluster_count_a", "cluster_count_b", "twi", "unchanged"} <= set(p)
    # stability_report has best_value + best_unchanged_pct.
    assert "best_value" in body["stability"]
    assert "best_unchanged_pct" in body["stability"]


def test_sensitivity_400_on_unsupported_field(client):
    _set_rules(client)
    resp = client.post(
        "/api/v1/sensitivity",
        json={
            "field": "not.a.field",
            "start": 0.5,
            "stop": 0.6,
            "step": 0.1,
            "sample_n": 10,
        },
    )
    assert resp.status_code == 400
    assert "Unsupported sweep field" in resp.json()["detail"]


def test_sensitivity_400_when_no_rules(client):
    # Don't seed rules — the AppState's loaded rules are reset by passing
    # an explicit empty payload-with-no-rules-on-state? No: the fixture's
    # rules.py loads goldenmatch.yml at app startup. Override by PUTing
    # a no-matchkey ruleset and then sweeping a matchkey field.
    client.put("/api/v1/rules", json={"threshold": 0.85, "matchkeys": []})
    resp = client.post(
        "/api/v1/sensitivity",
        json={
            "field": "matchkey.does_not_exist.threshold",
            "start": 0.5,
            "stop": 0.6,
            "step": 0.1,
            "sample_n": 10,
        },
    )
    assert resp.status_code == 400
    assert "not found" in resp.json()["detail"].lower()
