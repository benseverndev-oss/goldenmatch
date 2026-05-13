"""GET /api/v1/controller/telemetry — surface AutoConfigController output.

The endpoint is intentionally cheap: it serves whatever was stashed on
AppState by the most recent /api/v1/autoconfig or /api/v1/run?auto_config=true.
When no controller run has happened yet, it returns ``available=false`` and
empty lists rather than 404 — the workbench panel renders a neutral
"run auto-config to see decisions" state in that case.
"""
from __future__ import annotations


def test_telemetry_returns_unavailable_before_any_controller_run(client):
    resp = client.get("/api/v1/controller/telemetry")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["decisions"] == []
    assert body["column_priors"] == []
    assert body["negative_evidence"] == []
    assert body["stop_reason"] is None
    assert body["committed_matchkeys"] == []


def test_telemetry_populated_after_autoconfig(client):
    # Trigger auto-config so the controller runs.
    ac = client.post("/api/v1/autoconfig")
    assert ac.status_code == 200, ac.text

    resp = client.get("/api/v1/controller/telemetry")
    assert resp.status_code == 200
    body = resp.json()
    # Controller ran → telemetry is available, source labelled correctly.
    assert body["available"] is True
    assert body["source"] == "autoconfig"
    assert body["recorded_at"] is not None
    # Stop reason is one of the StopReason enum values; controller always
    # sets it before returning. Don't pin the exact value — the heuristic
    # path on the 3-row fixture could legitimately hit several of them.
    assert body["stop_reason"] in {
        "green", "converged", "budget_iterations", "budget_time",
        "policy_satisfied", "policy_no_progress", "oscillating", "cancelled",
    }
    # Committed matchkeys reflect the engine config the controller picked.
    assert isinstance(body["committed_matchkeys"], list)
    assert len(body["committed_matchkeys"]) >= 1
    # health verdict surfaces the controller's view of the committed config
    assert body["health"] in {"green", "yellow", "red"}
