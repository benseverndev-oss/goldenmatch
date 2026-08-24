"""A rule should predict the metric its action actually moves.

Splitting weak transitive bridges removes BRIDGES. It does not reliably raise
transitivity. Measured on Abt-Buy with the profile emitted after the split:

    bridge_edge_count   59  ->  30
    transitivity_rate   0.132 -> 0.141
    n_clusters          679 -> 709

The transitivity move is 0.009, barely outside the triple sampler's ~0.003-0.005
noise band, while bridges halve. A rule predicting transitivity for the SPLITTING
action is reading noise; one predicting bridges is reading the effect.

The threshold nudge is a different action and keeps predicting transitivity.
"""

from __future__ import annotations

import pytest
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    ClusterConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.autoconfig_history import (
    HistoryEntry,
    RunHistory,
    rule_effect_was_negative,
)
from goldenmatch.core.autoconfig_rules import rule_cluster_giant, rule_low_transitivity
from goldenmatch.core.complexity_profile import (
    BlockingProfile,
    ClusterProfile,
    ComplexityProfile,
    DataProfile,
    ScoringProfile,
)


def _cfg(threshold: float = 0.7, cluster: ClusterConfig | None = None) -> GoldenMatchConfig:
    return GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="mk", type="weighted", threshold=threshold,
                fields=[MatchkeyField(field="name", scorer="token_sort", weight=1.0)],
            )
        ],
        blocking=BlockingConfig(strategy="static",
                                keys=[BlockingKeyConfig(fields=["name"])]),
        cluster=cluster,
    )


def _profile(transitivity: float = 0.2, cluster_size_max: int = 4,
             bridges: int = 7) -> ComplexityProfile:
    return ComplexityProfile(
        data=DataProfile(n_rows=1000, n_cols=3),
        blocking=BlockingProfile(n_blocks=20, reduction_ratio=0.9),
        scoring=ScoringProfile(n_pairs_scored=500, candidates_compared=5000,
                               candidates_counted=True, mass_above_threshold=1.0,
                               dip_statistic=0.5),
        cluster=ClusterProfile(n_clusters=50, cluster_size_max=cluster_size_max,
                               transitivity_rate=transitivity,
                               bridge_edge_count=bridges),
    )


def test_low_transitivity_predicts_bridges_when_it_asks_for_splitting():
    out = rule_low_transitivity(_profile(), _cfg(), RunHistory())
    assert out is not None
    _, decision = out
    assert decision.config_diff == {"cluster.split_weak_bridges": True}
    assert decision.predicts == "cluster.bridge_edge_count"
    assert decision.predicts_direction == "down"


def test_low_transitivity_still_predicts_transitivity_for_the_threshold_fallback():
    """A different action moving a different metric.

    Note this fallback LOWERS the threshold (0.70 -> 0.65) where
    `rule_cluster_giant`'s RAISES it -- the two rules pull opposite ways on
    purpose, and the history guard is what stops either repeating uselessly.
    """
    out = rule_low_transitivity(
        _profile(), _cfg(cluster=ClusterConfig(split_weak_bridges=True)), RunHistory()
    )
    assert out is not None
    new_cfg, decision = out
    assert new_cfg.matchkeys[0].threshold == pytest.approx(0.65)
    assert decision.predicts == "cluster.transitivity_rate"
    assert decision.predicts_direction == "up"


def test_cluster_giant_predicts_bridges_when_it_asks_for_splitting():
    out = rule_cluster_giant(_profile(cluster_size_max=400), _cfg(), RunHistory())
    assert out is not None
    _, decision = out
    assert decision.config_diff == {"cluster.split_weak_bridges": True}
    assert decision.predicts == "cluster.bridge_edge_count"
    assert decision.predicts_direction == "down"


def test_cluster_giant_predicts_cluster_size_for_the_threshold_fallback():
    """Raising the threshold is meant to shrink the giant cluster, not to remove
    bridges -- splitting is already on by the time this branch runs."""
    out = rule_cluster_giant(
        _profile(cluster_size_max=400),
        _cfg(cluster=ClusterConfig(split_weak_bridges=True)),
        RunHistory(),
    )
    assert out is not None
    _, decision = out
    assert decision.predicts == "cluster.cluster_size_max"
    assert decision.predicts_direction == "down"


def _entry(iteration: int, profile: ComplexityProfile, decision=None) -> HistoryEntry:
    return HistoryEntry(iteration=iteration, config=None, profile=profile,
                        decision=decision, error=None, wall_clock_ms=1)


def test_a_split_that_removed_no_bridges_is_read_as_negative():
    """The point of all of this: the rule can now tell its action did nothing."""
    fired = rule_low_transitivity(_profile(bridges=7), _cfg(), RunHistory())
    assert fired is not None
    history = RunHistory()
    history.entries.append(_entry(0, _profile(bridges=7), fired[1]))
    history.entries.append(_entry(1, _profile(bridges=7)))
    assert rule_effect_was_negative(history, "low_transitivity") is True


def test_a_split_that_halved_bridges_is_not_negative():
    """The Abt-Buy shape: 59 -> 30."""
    fired = rule_low_transitivity(_profile(bridges=59), _cfg(), RunHistory())
    assert fired is not None
    history = RunHistory()
    history.entries.append(_entry(0, _profile(bridges=59), fired[1]))
    history.entries.append(_entry(1, _profile(bridges=30)))
    assert rule_effect_was_negative(history, "low_transitivity") is False
