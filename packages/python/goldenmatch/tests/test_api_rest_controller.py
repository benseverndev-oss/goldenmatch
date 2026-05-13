"""REST API controller-telemetry surface (v1.7-v1.12).

Direct unit tests against `MatchServer.autoconfigure` and
`MatchServer.get_controller_telemetry` — exercises the same code path the
new `POST /autoconfig` and `GET /controller/telemetry` endpoints invoke.
Avoids spinning up the stdlib HTTPServer to keep the test fast and free
of port-collision flakes.
"""
from __future__ import annotations

import csv
import tempfile

import pytest


@pytest.fixture
def server():
    """A MatchServer initialised with a 3-record fixture."""
    from goldenmatch.api.server import MatchServer
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        GoldenRulesConfig,
        MatchkeyConfig,
        MatchkeyField,
        OutputConfig,
    )
    from goldenmatch.tui.engine import MatchEngine

    with tempfile.NamedTemporaryFile(
        suffix=".csv", delete=False, mode="w", newline=""
    ) as f:
        w = csv.writer(f)
        w.writerow(["name", "email", "zip"])
        w.writerow(["John Smith", "john@test.com", "10001"])
        w.writerow(["Jon Smith", "jon@test.com", "10001"])
        w.writerow(["Jane Doe", "jane@test.com", "90210"])
        path = f.name

    engine = MatchEngine([path])
    config = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="test", type="weighted", threshold=0.80,
            fields=[
                MatchkeyField(field="name", scorer="jaro_winkler", weight=0.7, transforms=["lowercase"]),
                MatchkeyField(field="zip", scorer="exact", weight=0.3),
            ],
        )],
        blocking=BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])]),
        golden_rules=GoldenRulesConfig(default_strategy="most_complete"),
        output=OutputConfig(),
    )

    srv = MatchServer(engine, config)
    srv.initialize()
    return srv


def test_telemetry_unavailable_before_autoconfig(server):
    """GET /controller/telemetry returns the unavailable sentinel pre-autoconfig."""
    result = server.get_controller_telemetry()
    assert result["available"] is False


def test_autoconfig_returns_config_and_telemetry(server):
    """POST /autoconfig runs the controller and returns both halves."""
    result = server.autoconfigure()
    assert "config" in result
    assert "telemetry" in result
    # The telemetry blob shares its shape with the web / CLI / SQL surfaces.
    assert "available" in result["telemetry"]


def test_telemetry_persists_after_autoconfig(server):
    """The GET endpoint serves the cached blob from the POST call without
    re-running the controller.
    """
    posted = server.autoconfigure()
    fetched = server.get_controller_telemetry()
    # Cached blob is the same object (server stashes it inline).
    assert fetched == posted["telemetry"]


def test_autoconfig_with_explicit_records(server):
    """records= override autoconfigs a different dataset than the loaded one."""
    other_records = [
        {"name": "Alice Brown", "email": "ab@x.com", "zip": "30303"},
        {"name": "alice brown", "email": "ab@x.com", "zip": "30303"},
        {"name": "Bob Lee", "email": "bl@x.com", "zip": "30303"},
    ]
    result = server.autoconfigure(records=other_records)
    assert "config" in result
    assert "telemetry" in result
