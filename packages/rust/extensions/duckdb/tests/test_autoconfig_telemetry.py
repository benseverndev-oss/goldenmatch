"""Tests for the v1.7-v1.12 AutoConfig + telemetry surface.

Covers the four new UDFs registered by ``goldenmatch_duckdb.functions.register``:

  - ``goldenmatch_autoconfig(table)`` returns committed config JSON
  - ``goldenmatch_autoconfig_telemetry(table)`` returns telemetry JSON
  - ``goldenmatch_dedupe_full(table, config_json)`` runs the full pipeline
    with a `GoldenMatchConfig` JSON (supports `negative_evidence`)
  - ``gm_telemetry(job)`` returns the last `gm_run` telemetry for a job

These mirror the Postgres `goldenmatch_*` / `gm_*` functions and the
CLI's `goldenmatch autoconfig` subcommand.
"""
from __future__ import annotations

import json

import duckdb
import pytest

from goldenmatch_duckdb.functions import register


@pytest.fixture
def con():
    c = duckdb.connect()
    register(c)
    # Small but non-trivial fixture: clear identity column (email), within-
    # column case noise (first_name), typo variants (last_name).
    c.sql("""
        CREATE TABLE contacts AS
        SELECT * FROM (VALUES
            ('John', 'Smith', 'john@ex.com'),
            ('john', 'Smith', 'john@ex.com'),
            ('JOHN', 'Smyth', 'john@ex.com'),
            ('Jane', 'Doe', 'jane@t.com'),
            ('Bob', 'Jones', 'bob@t.com'),
            ('Robert', 'Brown', 'robert@t.com')
        ) AS t(first_name, last_name, email)
    """)
    return c


class TestAutoconfig:
    def test_returns_committed_config_json(self, con):
        """``goldenmatch_autoconfig`` returns a parseable JSON config."""
        result = con.sql("SELECT goldenmatch_autoconfig('contacts')").fetchone()[0]
        assert isinstance(result, str)
        config = json.loads(result)
        # Real config has matchkeys (could be `match_settings.matchkeys` or
        # top-level `matchkeys` depending on schema version — accept either).
        has_matchkeys = (
            "matchkeys" in config
            or ("match_settings" in config and "matchkeys" in (config["match_settings"] or {}))
        )
        assert has_matchkeys, f"no matchkeys in committed config: {config}"

    def test_telemetry_surfaces_stop_reason(self, con):
        """``goldenmatch_autoconfig_telemetry`` includes a StopReason value."""
        result = con.sql(
            "SELECT goldenmatch_autoconfig_telemetry('contacts')"
        ).fetchone()[0]
        telemetry = json.loads(result)
        assert telemetry["available"] is True
        # Controller always sets stop_reason; allow any valid enum value.
        valid = {
            "green", "converged", "budget_iterations", "budget_time",
            "policy_satisfied", "policy_no_progress", "oscillating", "cancelled",
        }
        assert telemetry["stop_reason"] in valid, telemetry


class TestDedupeFull:
    def test_runs_with_committed_config(self, con):
        """``goldenmatch_dedupe_full`` accepts the JSON ``autoconfig`` returns."""
        config_json = con.sql(
            "SELECT goldenmatch_autoconfig('contacts')"
        ).fetchone()[0]
        # Round-trip: feed the auto-config output straight into dedupe_full.
        # Expect either golden records JSON or stats JSON — both parse.
        result = con.sql(
            "SELECT goldenmatch_dedupe_full('contacts', ?)",
            params=[config_json],
        ).fetchone()[0]
        assert isinstance(result, str)
        parsed = json.loads(result)
        # Golden records emit as a list; stats fall through to a dict.
        assert isinstance(parsed, (list, dict))


class TestGmTelemetry:
    def test_unavailable_before_run(self, con):
        """``gm_telemetry`` returns the unavailable sentinel on a fresh job."""
        con.sql(
            "SELECT gm_configure('j1', ?)",
            params=[json.dumps({"exact": ["email"]})],
        ).fetchone()
        telemetry = json.loads(
            con.sql("SELECT gm_telemetry('j1')").fetchone()[0]
        )
        assert telemetry["available"] is False

    def test_telemetry_unknown_job(self, con):
        """``gm_telemetry`` on a job that doesn't exist also returns unavailable."""
        telemetry = json.loads(
            con.sql("SELECT gm_telemetry('does-not-exist')").fetchone()[0]
        )
        assert telemetry["available"] is False
