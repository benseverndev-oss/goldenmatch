"""Tests for Wave B v1.13 autoconfig (#124 + #125).

#124 spec: docs/superpowers/specs/2026-05-21-demote-rule-deletion-design.md
#125 spec: docs/superpowers/specs/2026-05-21-expand-sample-design.md
"""

from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.core.autoconfig_history import PolicyDecision

# ---------------------------------------------------------------------------
# #125: ExpandSample(2.0) action
# ---------------------------------------------------------------------------


def test_policy_decision_has_expand_sample_field():
    """PolicyDecision now carries optional expand_sample factor."""
    d = PolicyDecision(
        rule_name="r", rationale="x", config_diff={},
    )
    assert d.expand_sample is None  # default

    d2 = PolicyDecision(
        rule_name="r", rationale="x", config_diff={}, expand_sample=2.0,
    )
    assert d2.expand_sample == 2.0


def test_rule_sparse_match_expand_emits_expand_sample_factor():
    """rule_sparse_match_expand sets expand_sample=2.0 when it fires
    (was: only lowered threshold via _with_lower_threshold proxy)."""
    from goldenmatch.config.schemas import (
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )
    from goldenmatch.core.autoconfig_history import RunHistory
    from goldenmatch.core.autoconfig_rules import rule_sparse_match_expand
    from goldenmatch.core.complexity_profile import (
        BlockingProfile,
        ComplexityProfile,
        DataProfile,
        ScoringProfile,
        SparsityVerdict,
    )

    df = pl.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})

    class _Ctx:
        sparsity_verdict = SparsityVerdict(is_sparse=True, estimated_n_true_pairs=5)
        _fired: set = set()  # type: ignore[type-arg]

        def has_fired(self, rule_name: str) -> bool:
            return rule_name in self._fired

        def mark_fired(self, rule_name: str) -> None:
            self._fired.add(rule_name)

        _df = df
        column_priors: dict = {}

    profile = ComplexityProfile(
        data=DataProfile(n_rows=200, n_cols=2),
        blocking=BlockingProfile(),
        scoring=ScoringProfile(),
    )
    # Real matchkey with non-None threshold so _with_lower_threshold
    # returns a distinct new_cfg (rule returns None otherwise).
    # Use type=exact so the schema doesn't require a blocking config
    # (weighted/probabilistic do; exact doesn't). _with_lower_threshold
    # works on any matchkey type as long as threshold is not None.
    current = GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="m",
                type="exact",
                threshold=0.8,
                fields=[MatchkeyField(field="a")],
            ),
        ],
    )
    history = RunHistory()

    result = rule_sparse_match_expand(profile, current, history, ctx=_Ctx())  # type: ignore[arg-type]
    assert result is not None, "rule should fire on sparse fixture"
    _new_cfg, decision = result
    assert decision.expand_sample == 2.0
    assert "ExpandSample(2.0)" in decision.rationale


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
