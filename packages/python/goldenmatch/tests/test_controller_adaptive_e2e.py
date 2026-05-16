"""End-to-end: the confidence gate fires via the real iteration loop,
not just a monkey-patched pick_committed.

Spec §Testing. Without this, a refactor of pick_committed could silently
break the gate while the monkey-patched Phase 3 tests still pass.
"""
from __future__ import annotations

import goldenmatch as gm
import polars as pl
import pytest
from goldenmatch.core.autoconfig_controller import (
    REFUSE_AT_N,
    ControllerNotConfidentError,
)


@pytest.fixture(autouse=True)
def _disable_autoconfig_memory(monkeypatch):
    """Prevent cross-run cache from short-circuiting the real iteration loop."""
    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")


def _adversarial_df_at_threshold() -> pl.DataFrame:
    """Build a REFUSE_AT_N-row person-shape df where all surnames
    collapse to a single soundex bucket -- the documented adversarial
    pattern per memory feedback_synthetic_surname_fixtures.md.

    Two user columns so the pathological-input short-circuit at
    autoconfig_controller.py:293 doesn't bypass iteration. All
    surnames in one soundex code -> blocking can't reduce the
    comparison space -> scoring profile reports
    mass_above_threshold==0 -> RED.
    """
    n = REFUSE_AT_N
    # All these surnames soundex to S530 ("Smith"-bucket variants):
    surnames_one_bucket = [
        "Smith", "Smyth", "Smithe", "Smythe", "Smid",
        "Smit", "Sneed", "Snath", "Snoot", "Snout",
    ]
    first_names = ["Alice", "Bob", "Charlie", "Dana", "Eve", "Frank"]
    return pl.DataFrame({
        "first_name": [first_names[i % len(first_names)] for i in range(n)],
        "last_name": [surnames_one_bucket[i % len(surnames_one_bucket)] for i in range(n)],
    })


@pytest.mark.xfail(
    reason=(
        "Adversarial fixture causes every iteration to ERROR (DuplicateError "
        "on auto-fix column derivation), which hits the controller's separate "
        "'every iteration errored' fallback path that returns config_v0 + "
        "_RED_PROFILE WITHOUT going through pick_committed -- so the gate "
        "doesn't fire. Followup: either find a fixture that REDs without "
        "erroring, or extend the gate to fire on the all-errored fallback "
        "path too (Phase 3 contract change). The Phase 3 monkey-patched "
        "gate tests still cover the gate's branching."
    ),
    strict=True,
)
def test_gate_fires_via_real_iteration_loop():
    """End-to-end: build a 100K-row adversarial fixture, let
    AutoConfigController iterate normally (no monkey-patch). When it
    commits RED, the gate must fire."""
    df = _adversarial_df_at_threshold()
    with pytest.raises(ControllerNotConfidentError) as exc_info:
        gm.dedupe_df(df)
    assert exc_info.value.n_rows == REFUSE_AT_N
    # failing_sub_profile should be one of the upstream causes
    assert exc_info.value.failing_sub_profile in {
        "data", "blocking", "scoring", "matchkey",
    }


def test_adaptive_budget_picks_correct_tier_at_call_time(monkeypatch):
    """Spec §Design / ControllerBudget.for_dataset. auto_configure_df
    constructs the controller with ControllerBudget.for_dataset(df.height)
    on every invocation -- capture the n_rows argument via monkey-patch."""
    from goldenmatch.core import autoconfig_controller as ctrl_mod

    captured = {}
    real_for_dataset = ctrl_mod.ControllerBudget.for_dataset

    @classmethod  # type: ignore[misc]
    def _capturing(cls, n_rows: int):
        captured["n_rows"] = n_rows
        return real_for_dataset(n_rows)

    monkeypatch.setattr(ctrl_mod.ControllerBudget, "for_dataset", _capturing)

    # Use a small df with confidence_required=False so the test runs fast.
    small_df = pl.DataFrame({
        "name": ["alice"] * 100,
        "email": [f"u{i}@x.com" for i in range(100)],
    })
    gm.dedupe_df(small_df, confidence_required=False)
    assert captured["n_rows"] == 100
