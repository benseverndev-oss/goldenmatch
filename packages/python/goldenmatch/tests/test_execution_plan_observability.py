"""Phase 6: ExecutionPlan surfaces on every consumer-facing channel.

Spec §Observability: planner decisions must be observable from CLI, MCP,
web telemetry, and PostflightReport string rendering.
"""
from __future__ import annotations

import goldenmatch as gm
import polars as pl
from goldenmatch.cli._controller_render import (
    _execution_plan,
    _execution_plan_short,
    render_short_status,
)
from goldenmatch.core.autoconfig import _LAST_CONTROLLER_RUN
from goldenmatch.core.autoconfig_history import RunHistory
from goldenmatch.core.execution_plan import ExecutionPlan
from goldenmatch.web.controller_telemetry import serialize_telemetry


def _df() -> pl.DataFrame:
    return pl.DataFrame({
        "name": ["alice", "alyce", "bob", "robert"] * 20,
        "email": [f"u{i}@x.com" for i in range(80)],
    })


# ── PostflightReport.__str__ ────────────────────────────────────────────────


def test_postflight_str_includes_plan_line_after_dedupe():
    """Running zero-config dedupe should leave a 'Plan: ...' line in the
    PostflightReport string render (the line CLI shows to users)."""
    result = gm.dedupe_df(_df())
    pf = result.postflight_report
    assert pf is not None, "zero-config dedupe should have PostflightReport"
    rendered = str(pf)
    assert "Plan:" in rendered, (
        f"PostflightReport.__str__ should surface the ExecutionPlan; "
        f"got:\n{rendered}"
    )
    assert "backend=" in rendered


def test_postflight_str_omits_plan_line_when_history_absent():
    """Hand-written-config path: no controller_history -> no Plan line."""
    from goldenmatch.core.autoconfig_verify import PostflightReport

    pf = PostflightReport()
    assert "Plan:" not in str(pf)


# ── web/controller_telemetry.serialize_telemetry ───────────────────────────


def test_serialize_telemetry_includes_execution_plan_key():
    """Every cross-surface telemetry consumer reads through this serializer
    (per CLAUDE.md). The execution_plan key must be present."""
    history = RunHistory()
    history.execution_plan = ExecutionPlan(
        backend="chunked",
        chunk_size=250_000,
        max_workers=16,
        pair_spill_threshold="ram",
        clustering_strategy="in_memory",
        rule_name="plan_selected_chunked",
    )
    blob = serialize_telemetry(
        profile=None,
        history=history,
        committed_config=None,
        source=None,
        run_name=None,
        recorded_at=None,
    )
    assert "execution_plan" in blob
    plan_dict = blob["execution_plan"]
    assert plan_dict is not None
    assert plan_dict["rule_name"] == "plan_selected_chunked"
    assert plan_dict["backend"] == "chunked"
    assert plan_dict["chunk_size"] == 250_000
    assert plan_dict["max_workers"] == 16
    assert plan_dict["pair_spill_threshold"] == "ram"
    assert plan_dict["clustering_strategy"] == "in_memory"


def test_serialize_telemetry_returns_none_for_pre_v3_history():
    """Histories without execution_plan field still serialize cleanly."""
    history = RunHistory()  # execution_plan default = None
    blob = serialize_telemetry(
        profile=None,
        history=history,
        committed_config=None,
        source=None,
        run_name=None,
        recorded_at=None,
    )
    assert blob["execution_plan"] is None


def test_serialize_telemetry_no_history_returns_none():
    blob = serialize_telemetry(
        profile=None,
        history=None,
        committed_config=None,
        source=None,
        run_name=None,
        recorded_at=None,
    )
    assert blob["execution_plan"] is None


# ── CLI _controller_render ────────────────────────────────────────────────


def test_cli_execution_plan_renders_rule_and_backend():
    history = RunHistory()
    history.execution_plan = ExecutionPlan(
        backend="duckdb",
        max_workers=8,
        pair_spill_threshold="duckdb",
        clustering_strategy="partitioned_union_find",
        rule_name="plan_selected_duckdb",
    )
    row = _execution_plan(history)
    assert row is not None
    # Rich Text -- inspect plain text (markup stripped).
    plain = row.plain
    assert "plan_selected_duckdb" in plain
    assert "duckdb" in plain
    assert "max_workers=8" in plain
    # non-default clustering is shown
    assert "partitioned_union_find" in plain


def test_cli_execution_plan_omits_default_clustering():
    """In-memory clustering is the default; don't clutter the panel with it."""
    history = RunHistory()
    history.execution_plan = ExecutionPlan(
        backend="polars-direct",
        max_workers=4,
        rule_name="plan_selected_simple",
    )
    row = _execution_plan(history)
    assert row is not None
    assert "clustering=" not in row.plain


def test_cli_execution_plan_returns_none_when_history_missing():
    assert _execution_plan(None) is None


def test_cli_execution_plan_short_returns_one_token():
    history = RunHistory()
    history.execution_plan = ExecutionPlan(
        backend="ray",
        rule_name="plan_selected_ray",
    )
    assert _execution_plan_short(history) == "plan=plan_selected_ray/ray"


def test_cli_render_short_status_includes_plan_token():
    """End-to-end: render_short_status surfaces the plan alongside health
    and stop_reason so a CI log line carries the full controller verdict."""
    gm.dedupe_df(_df())
    state = _LAST_CONTROLLER_RUN.get()
    assert state is not None
    _profile, history = state
    line = render_short_status(profile=_profile, history=history, committed_config=None)
    assert "plan=" in line
