"""Task 11: planned throughput posture surfacing tests (#1083).

Covers:
  - ExecutionPlan.sketch_metric field + apply_throughput_overlay sets it
  - _throughput_summary (telemetry helper)
  - serialize_telemetry includes "throughput" key
  - _render_throughput_line (PostflightReport.__str__ bonus line)
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# Step 1: sketch_metric field on ExecutionPlan + overlay sets it
# ---------------------------------------------------------------------------

def test_overlay_sets_sketch_metric():
    from goldenmatch.core.execution_plan import ExecutionPlan
    from goldenmatch.core.autoconfig_planner import apply_throughput_overlay
    from goldenmatch.config.schemas import ThroughputConfig
    p = apply_throughput_overlay(
        ExecutionPlan(), ThroughputConfig(enabled=True), metric="cosine", signature_len=256
    )
    assert p.sketch_metric == "cosine"


def test_overlay_sets_sketch_metric_jaccard():
    from goldenmatch.core.execution_plan import ExecutionPlan
    from goldenmatch.core.autoconfig_planner import apply_throughput_overlay
    from goldenmatch.config.schemas import ThroughputConfig
    p = apply_throughput_overlay(
        ExecutionPlan(), ThroughputConfig(enabled=True), metric="jaccard", signature_len=128
    )
    assert p.sketch_metric == "jaccard"


def test_execution_plan_sketch_metric_defaults_none():
    from goldenmatch.core.execution_plan import ExecutionPlan
    plan = ExecutionPlan()
    assert plan.sketch_metric is None


def test_execution_plan_sketch_metric_set_directly():
    from goldenmatch.core.execution_plan import ExecutionPlan
    import dataclasses
    plan = dataclasses.replace(ExecutionPlan(), sketch_metric="jaccard")
    assert plan.sketch_metric == "jaccard"


# ---------------------------------------------------------------------------
# Step 2: _throughput_summary (telemetry helper)
# ---------------------------------------------------------------------------

def test_throughput_summary_from_plan():
    from goldenmatch.core.execution_plan import ExecutionPlan
    from goldenmatch.web.controller_telemetry import _throughput_summary
    plan = ExecutionPlan(
        verify_mode="sketch_distance",
        sketch_metric="jaccard",
        sketch_bands=16,
        sketch_rows=8,
        sketch_similarity=0.8,
    )
    s = _throughput_summary(plan)
    assert s is not None
    assert s["metric"] == "jaccard"
    assert s["bands"] == 16
    assert s["rows_per_band"] == 8
    assert s["similarity_threshold"] == 0.8
    assert s["verify_mode"] == "sketch_distance"
    assert 0.0 <= s["expected_recall"] <= 1.0


def test_throughput_summary_cosine_plan():
    from goldenmatch.core.execution_plan import ExecutionPlan
    from goldenmatch.web.controller_telemetry import _throughput_summary
    plan = ExecutionPlan(
        verify_mode="sketch_distance",
        sketch_metric="cosine",
        sketch_bands=8,
        sketch_rows=16,
        sketch_similarity=0.85,
    )
    s = _throughput_summary(plan)
    assert s is not None
    assert s["metric"] == "cosine"
    assert 0.0 <= s["expected_recall"] <= 1.0


def test_throughput_summary_none_when_no_plan():
    from goldenmatch.web.controller_telemetry import _throughput_summary
    assert _throughput_summary(None) is None


def test_throughput_summary_none_when_full_mode():
    from goldenmatch.core.execution_plan import ExecutionPlan
    from goldenmatch.web.controller_telemetry import _throughput_summary
    # Default plan has verify_mode="full"
    assert _throughput_summary(ExecutionPlan()) is None


def test_throughput_summary_none_when_sketch_mode_no_metric():
    """Even in sketch_distance mode, expected_recall is None if bands/rows/sim missing."""
    from goldenmatch.core.execution_plan import ExecutionPlan
    from goldenmatch.web.controller_telemetry import _throughput_summary
    # sketch_distance but no bands/rows/sim set
    plan = ExecutionPlan(verify_mode="sketch_distance")
    s = _throughput_summary(plan)
    # Should return a dict (mode is right) but expected_recall is None
    assert s is not None
    assert s["expected_recall"] is None


def test_throughput_summary_falls_back_to_jaccard_when_no_metric():
    """When sketch_metric is None but mode is sketch_distance, fall back to jaccard."""
    from goldenmatch.core.execution_plan import ExecutionPlan
    from goldenmatch.web.controller_telemetry import _throughput_summary
    plan = ExecutionPlan(
        verify_mode="sketch_distance",
        sketch_bands=16,
        sketch_rows=8,
        sketch_similarity=0.8,
    )
    s = _throughput_summary(plan)
    assert s is not None
    assert s["metric"] == "jaccard"
    assert 0.0 <= s["expected_recall"] <= 1.0


# ---------------------------------------------------------------------------
# Step 2b: serialize_telemetry includes "throughput" key
# ---------------------------------------------------------------------------

def test_serialize_telemetry_includes_throughput_key_when_no_plan():
    """The "throughput" key is always present (may be None)."""
    from goldenmatch.web.controller_telemetry import serialize_telemetry
    result = serialize_telemetry(
        profile=None,
        history=None,
        committed_config=None,
        source=None,
        run_name=None,
        recorded_at=None,
    )
    assert "throughput" in result


def test_serialize_telemetry_throughput_none_when_no_config():
    from goldenmatch.web.controller_telemetry import serialize_telemetry
    result = serialize_telemetry(
        profile=None,
        history=None,
        committed_config=None,
        source="test",
        run_name="r1",
        recorded_at=None,
    )
    assert result["throughput"] is None


def test_serialize_telemetry_throughput_populated_from_config_plan():
    """When committed_config._throughput_plan has sketch_distance, telemetry shows it."""
    from goldenmatch.core.execution_plan import ExecutionPlan
    from goldenmatch.web.controller_telemetry import serialize_telemetry

    class _FakeConfig:
        _throughput_plan = ExecutionPlan(
            verify_mode="sketch_distance",
            sketch_metric="jaccard",
            sketch_bands=16,
            sketch_rows=8,
            sketch_similarity=0.8,
        )

        def get_matchkeys(self):
            return []

    result = serialize_telemetry(
        profile=None,
        history=None,
        committed_config=_FakeConfig(),
        source="test",
        run_name="r2",
        recorded_at=None,
    )
    tp = result["throughput"]
    assert tp is not None
    assert tp["metric"] == "jaccard"
    assert tp["bands"] == 16
    assert 0.0 <= tp["expected_recall"] <= 1.0


# ---------------------------------------------------------------------------
# Step 3: _render_throughput_line (PostflightReport.__str__ bonus)
# ---------------------------------------------------------------------------

def test_render_throughput_line():
    from goldenmatch.core.execution_plan import ExecutionPlan
    from goldenmatch.core.autoconfig_verify import _render_throughput_line
    plan = ExecutionPlan(
        verify_mode="sketch_distance",
        sketch_metric="jaccard",
        sketch_bands=16,
        sketch_rows=8,
        sketch_similarity=0.8,
    )
    line = _render_throughput_line(plan)
    assert "throughput" in line.lower()
    assert "jaccard" in line
    assert "16" in line
    assert "8" in line


def test_render_throughput_line_none():
    from goldenmatch.core.autoconfig_verify import _render_throughput_line
    assert _render_throughput_line(None) == ""


def test_render_throughput_line_full_mode():
    from goldenmatch.core.execution_plan import ExecutionPlan
    from goldenmatch.core.autoconfig_verify import _render_throughput_line
    # Default plan has verify_mode="full" -> empty
    assert _render_throughput_line(ExecutionPlan()) == ""


def test_render_throughput_line_has_expected_recall():
    from goldenmatch.core.execution_plan import ExecutionPlan
    from goldenmatch.core.autoconfig_verify import _render_throughput_line
    plan = ExecutionPlan(
        verify_mode="sketch_distance",
        sketch_metric="cosine",
        sketch_bands=8,
        sketch_rows=16,
        sketch_similarity=0.85,
    )
    line = _render_throughput_line(plan)
    assert "expected_recall" in line
    assert "0.8" in line  # similarity present


def test_postflight_report_str_includes_throughput_when_plan_is_set():
    """PostflightReport.__str__ emits a throughput line when controller_history
    carries an execution_plan with verify_mode='sketch_distance'."""
    from goldenmatch.core.execution_plan import ExecutionPlan
    from goldenmatch.core.autoconfig_verify import PostflightReport

    class _FakeHistory:
        execution_plan = ExecutionPlan(
            verify_mode="sketch_distance",
            sketch_metric="jaccard",
            sketch_bands=16,
            sketch_rows=8,
            sketch_similarity=0.8,
        )

        def pick_committed(self):
            return None

    report = PostflightReport()
    report.controller_history = _FakeHistory()
    text = str(report)
    assert "throughput" in text.lower()
    assert "jaccard" in text
