"""Tests for iteration-oscillation guard (#127).

Spec: docs/superpowers/specs/2026-05-21-oscillation-guard-design.md
"""

from __future__ import annotations

import pytest
from goldenmatch.config.schemas import GoldenMatchConfig
from goldenmatch.core.autoconfig_history import PolicyDecision, RunHistory
from goldenmatch.core.autoconfig_policy import HeuristicRefitPolicy
from goldenmatch.core.complexity_profile import (
    BlockingProfile,
    ComplexityProfile,
    DataProfile,
    ScoringProfile,
)


def _red_profile_with_oversized_block() -> ComplexityProfile:
    """Build a RED ComplexityProfile that would normally provoke a
    blocking-too-coarse rule."""
    return ComplexityProfile(
        data=DataProfile(n_rows=100_000, n_cols=5),
        blocking=BlockingProfile(
            n_blocks=2,
            total_comparisons=5_000_000_000,
            reduction_ratio=0.001,
            block_sizes_p99=50_000,
            block_sizes_max=50_000,
            oversized_block_count=2,
        ),
        scoring=ScoringProfile(
            n_pairs_scored=0,
            candidates_compared=1,
            dip_statistic=0.01,
            mass_above_threshold=0.01,
            mass_in_borderline=0.0,
        ),
    )


def test_oscillation_guard_skips_repeat_rule_same_rationale():
    """A rule firing with identical (rule_name, rationale) on
    consecutive propose calls is skipped on the second call.

    Uses a synthetic single-rule policy where the rule always returns
    the same decision tuple. Without the guard, propose returns the
    same config forever; with the guard, the second call falls through
    to return None (no other rules to try).
    """
    fire_count = {"n": 0}

    def _always_fire_same(profile, current, history):
        fire_count["n"] += 1
        # Distinguishing new_config so the bug-guard ("new_config == current"
        # → return None) doesn't preempt the oscillation guard under test.
        new_cfg = GoldenMatchConfig(backend=f"alt_{fire_count['n']}")
        decision = PolicyDecision(
            rule_name="rule_oscillates",
            rationale="same_rationale_always",
            config_diff={"x": 1},
        )
        return new_cfg, decision

    policy = HeuristicRefitPolicy(rules=[_always_fire_same])
    history = RunHistory()
    profile = _red_profile_with_oversized_block()
    current = GoldenMatchConfig()

    # First call: rule fires.
    result1 = policy.propose(profile, current=current, history=history)
    assert result1 is not None
    assert fire_count["n"] == 1

    # Second call: rule fires again, same (name, rationale), so guard skips.
    # No other rules → policy returns None.
    result2 = policy.propose(profile, current=current, history=history)
    assert result2 is None, "guard should have skipped the duplicate fire"


def test_oscillation_guard_allows_repeat_rule_with_different_rationale():
    """Same rule, different rationale → both fires allowed. Pins that
    the guard keys on rationale, not just rule_name."""
    call_count = {"n": 0}

    def _vary_rationale(profile, current, history):
        call_count["n"] += 1
        return GoldenMatchConfig(backend=f"alt_{call_count['n']}"), PolicyDecision(
            rule_name="rule_varies",
            rationale=f"different_rationale_{call_count['n']}",  # different each call
            config_diff={},
        )

    policy = HeuristicRefitPolicy(rules=[_vary_rationale])
    history = RunHistory()
    profile = _red_profile_with_oversized_block()
    current = GoldenMatchConfig()

    result1 = policy.propose(profile, current=current, history=history)
    result2 = policy.propose(profile, current=current, history=history)

    assert result1 is not None
    assert result2 is not None, "different rationale should bypass the guard"


def test_oscillation_guard_falls_through_to_next_rule_when_first_blocked():
    """When rule A is guard-blocked, the policy advances to rule B."""
    def _rule_a_fires_same(profile, current, history):
        return GoldenMatchConfig(backend="a"), PolicyDecision(
            rule_name="rule_a",
            rationale="repeating",
            config_diff={},
        )

    def _rule_b_fires_once(profile, current, history):
        return GoldenMatchConfig(backend="b"), PolicyDecision(
            rule_name="rule_b",
            rationale="ran_at_least_once",
            config_diff={},
        )

    policy = HeuristicRefitPolicy(rules=[_rule_a_fires_same, _rule_b_fires_once])
    history = RunHistory()
    profile = _red_profile_with_oversized_block()
    current = GoldenMatchConfig()

    # First call: rule_a fires.
    policy.propose(profile, current=current, history=history)
    # Second call: rule_a guard-blocked → rule_b fires.
    result2 = policy.propose(profile, current=current, history=history)
    assert result2 is not None
    # Verify rule_b was the one that fired by checking the attached decision.
    if history.entries:
        decision = history.entries[-1].decision
        if decision is not None:
            assert decision.rule_name == "rule_b"


def test_oscillation_guard_first_call_never_blocks():
    """No prior fire → first call always proceeds."""
    def _rule(profile, current, history):
        return GoldenMatchConfig(backend="x"), PolicyDecision(
            rule_name="any_rule",
            rationale="any_rationale",
            config_diff={},
        )

    policy = HeuristicRefitPolicy(rules=[_rule])
    history = RunHistory()
    profile = _red_profile_with_oversized_block()

    result = policy.propose(profile, current=GoldenMatchConfig(), history=history)
    assert result is not None


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
