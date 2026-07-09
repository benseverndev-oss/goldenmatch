"""Stage D.2: the fused-match routing post-step inside AutoConfigController.run.

The pivotal property: the post-step fires AFTER the backend plan is chosen,
regardless of whether the native autoconfig kernel or the Python planner rules
picked the backend -- so it is NOT dead under native-default-on autoconfig (the
whole reason it is a post-step, not a DEFAULT_RULES rule).

These tests patch ``maybe_route_fused_match`` (the routing decision) directly, so
they isolate the post-step's wiring (does it call the decision, apply the flag,
and stamp the rule_name?) from the est-RSS/coverage logic exercised in
tests/test_fused_routing.py.
"""

from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.core.autoconfig_controller import AutoConfigController, ControllerBudget
from goldenmatch.core.autoconfig_policy import HeuristicRefitPolicy
from goldenmatch.core.execution_plan import ExecutionPlan


def _df() -> pl.DataFrame:
    # Small multi-column frame that clears the pathological gates and reaches the
    # planner + post-step. Some duplicate-ish rows so blocking/scoring do work.
    names = ["alice", "bob", "carol", "dave", "erin", "frank", "grace", "heidi"]
    return pl.DataFrame(
        {
            "name": [names[i % len(names)] for i in range(40)],
            "city": [["nyc", "la", "sf", "chi"][i % 4] for i in range(40)],
            "zip": [str(10000 + (i % 12)) for i in range(40)],
        }
    )


def _controller() -> AutoConfigController:
    return AutoConfigController(policy=HeuristicRefitPolicy(), budget=ControllerBudget())


def _run(controller, df, **kw):
    # skip_finalize mirrors the _api zero-config path (no full-data finalize run).
    return controller.run(df, skip_finalize=True, confidence_required=False, **kw)


def test_post_step_sets_flag_when_route_true(monkeypatch):
    monkeypatch.setattr(
        "goldenmatch.core.fused_routing.maybe_route_fused_match",
        lambda **kw: True,
    )
    committed, _profile, history = _run(_controller(), _df(), fused_match_allowed=True)
    assert getattr(committed, "_use_fused_match", False) is True
    assert history.execution_plan is not None
    assert "fused_match_post_step" in (history.execution_plan.rule_name or "")
    assert history.execution_plan.use_fused_match is True


def test_post_step_no_flag_when_route_false(monkeypatch):
    monkeypatch.setattr(
        "goldenmatch.core.fused_routing.maybe_route_fused_match",
        lambda **kw: False,
    )
    committed, _profile, history = _run(_controller(), _df(), fused_match_allowed=True)
    assert getattr(committed, "_use_fused_match", False) is False
    assert "fused_match_post_step" not in (history.execution_plan.rule_name or "")


def test_post_step_fires_under_native_chosen_plan(monkeypatch):
    """KEY test: simulate the native kernel choosing the backend plan (patch
    ``apply_planner_rules`` to return a native-labeled plan). The post-step must
    STILL run and set the flag -- proving it is not short-circuited away by the
    native autoconfig path."""
    native_plan = ExecutionPlan(backend="polars-direct", rule_name="native:autoconfig_decide_plan")
    monkeypatch.setattr(
        "goldenmatch.core.autoconfig_planner.apply_planner_rules",
        lambda **kw: native_plan,
    )
    monkeypatch.setattr(
        "goldenmatch.core.fused_routing.maybe_route_fused_match",
        lambda **kw: True,
    )
    committed, _profile, history = _run(_controller(), _df(), fused_match_allowed=True)
    assert getattr(committed, "_use_fused_match", False) is True
    # The post-step stamps its marker ON TOP of the native-chosen rule_name.
    rule = history.execution_plan.rule_name or ""
    assert rule.startswith("native:autoconfig_decide_plan")
    assert rule.endswith("+fused_match_post_step")


def test_default_deny_absent_hint(monkeypatch):
    """When fused_match_allowed defaults False (non-DF/CLI paths), needs_artifacts
    is forced True -> the post-step's decision receives needs_artifacts=True. We
    assert the decision is invoked with needs_artifacts=True so routing can never
    fire on the default-deny path."""
    seen = {}

    def _spy(**kw):
        seen.update(kw)
        return False

    monkeypatch.setattr("goldenmatch.core.fused_routing.maybe_route_fused_match", _spy)
    committed, _profile, _history = _run(_controller(), _df())  # fused_match_allowed defaults False
    assert seen.get("needs_artifacts") is True
    assert getattr(committed, "_use_fused_match", False) is False


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
