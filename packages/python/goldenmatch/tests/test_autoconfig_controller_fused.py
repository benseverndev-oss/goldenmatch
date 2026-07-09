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
from goldenmatch.core.complexity_profile import StopReason
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


# 60-name surname pool spread across soundex codes (per the synthetic-fixture rule
# — same-soundex surnames hang blocking). Enough distinct keys to keep blocks small.
_SURNAMES = [
    "Anderson", "Baker", "Carter", "Diaz", "Edwards", "Fisher", "Garcia", "Hughes",
    "Ibrahim", "Jackson", "Klein", "Lopez", "Murphy", "Nguyen", "Owens", "Patel",
    "Quinn", "Reyes", "Sullivan", "Turner", "Underwood", "Vargas", "Walsh", "Xiong",
    "Young", "Zimmerman", "Bishop", "Chandler", "Delgado", "Emerson", "Franklin",
    "Gallagher", "Hoffman", "Ingram", "Jennings", "Kirkland", "Lombardi", "Mercer",
    "Norton", "Osborne", "Pearson", "Quintero", "Ramsey", "Schneider", "Thornton",
    "Ulrich", "Valentine", "Whitaker", "Yamamoto", "Zamora",
]


def _exact_dup_df(n_people: int, copies: int = 3) -> pl.DataFrame:
    """`n_people` distinct people, EACH repeated `copies` times. copies=3 makes
    within-block candidate pairs >= 0.5*n_rows so the controller's GREEN-path
    `_suspicious_tight_blocking` override does NOT divert the break (it needs
    candidates < 0.5*n_rows), giving a clean GREEN stop that rebinds the loop-local
    `n_rows` to the sample height -- the exact condition finding 1 regresses on."""
    rows = []
    for i in range(n_people):
        rec = {
            "name": f"{_SURNAMES[i % len(_SURNAMES)]}{i}",
            "zip": str(10000 + (i % 500)),
        }
        for _ in range(copies):
            rows.append(dict(rec))
    return pl.DataFrame(rows)


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


def test_post_step_receives_full_count_not_sample_on_green(monkeypatch):
    """REGRESSION (finding 1): on the GREEN stop path the loop-local `n_rows` is
    rebound to the SAMPLE height (profile_n.data.n_rows). The post-step must use the
    AUTHORITATIVE full-data count (`_current_run_full_n_rows`), NOT the sample --
    otherwise classic-RSS is under-estimated and match routing silently fails to
    fire on a large clean dataset that greens on its sample."""
    from goldenmatch.core.complexity_profile import ComplexityProfile, HealthVerdict

    captured: dict = {}

    def _spy(**kw):
        captured["n_rows"] = kw.get("n_rows")
        return False

    monkeypatch.setattr("goldenmatch.core.fused_routing.maybe_route_fused_match", _spy)
    # Force the GREEN stop path deterministically (real GREEN is data-dependent and
    # flaky). The iter-0 GREEN branch rebinds the loop-local `n_rows` to the SAMPLE
    # height at controller line 837 -- the bug's trigger.
    monkeypatch.setattr(ComplexityProfile, "health", lambda self: HealthVerdict.GREEN)

    # Force sampling: full (1800) > sample (400).
    controller = AutoConfigController(
        policy=HeuristicRefitPolicy(),
        budget=ControllerBudget(sample_size_default=400, sample_skip_below=1000),
    )
    df = _exact_dup_df(600, copies=3)  # 1800 rows
    full_height = df.height
    _committed, _profile, history = controller.run(
        df, skip_finalize=True, confidence_required=False, fused_match_allowed=True
    )
    # Prove the GREEN break actually fired (so the loop-local n_rows WAS rebound to
    # the sample) -- otherwise this test would pass trivially.
    assert history.stop_reason == StopReason.GREEN
    # The post-step must have seen the FULL row count, never the sample height.
    assert captured.get("n_rows") == full_height
    assert captured["n_rows"] == getattr(controller, "_current_run_full_n_rows", None)
    assert full_height > 400  # sampling really happened


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
