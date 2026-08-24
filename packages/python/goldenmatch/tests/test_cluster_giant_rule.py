"""`cluster_size_max > 0.1 * n_rows` is RED and nothing answered it.

`rule_low_transitivity` was the only rule reading `profile.cluster`, and it
returns None unless `transitivity_rate < 0.85`. So a run where one cluster
swallowed 10%+ of the data, with healthy transitivity, produced no proposal at
all -- the controller reported RED and did nothing with it.
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
from goldenmatch.core.autoconfig_history import RunHistory
from goldenmatch.core.autoconfig_rules import rule_cluster_giant
from goldenmatch.core.complexity_profile import (
    BlockingProfile,
    ClusterProfile,
    ComplexityProfile,
    DataProfile,
    HealthVerdict,
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


def _profile(n_rows: int, cluster_size_max: int) -> ComplexityProfile:
    return ComplexityProfile(
        data=DataProfile(n_rows=n_rows, n_cols=3),
        blocking=BlockingProfile(n_blocks=20, reduction_ratio=0.9),
        scoring=ScoringProfile(n_pairs_scored=500, candidates_compared=5000,
                               candidates_counted=True, mass_above_threshold=1.0,
                               dip_statistic=0.5),
        cluster=ClusterProfile(n_clusters=10, cluster_size_max=cluster_size_max,
                               transitivity_rate=1.0),
    )


def test_the_fixture_is_actually_the_red_this_rule_answers():
    """Guard the premise: if the fixture were not RED for `cluster_giant`, every
    assertion below would be testing a condition that never occurs."""
    profile = _profile(n_rows=1000, cluster_size_max=400)
    assert profile.cluster.health(n_rows=1000) == HealthVerdict.RED
    assert profile.cluster.red_reason(n_rows=1000) == "cluster_giant"


def test_fires_on_a_giant_cluster_and_asks_for_splitting():
    out = rule_cluster_giant(_profile(n_rows=1000, cluster_size_max=400), _cfg(),
                             RunHistory())
    assert out is not None
    new_cfg, decision = out
    assert new_cfg.cluster is not None
    assert new_cfg.cluster.split_weak_bridges is True
    assert new_cfg.matchkeys[0].threshold == pytest.approx(0.7), (
        "the cluster action must be offered ALONE -- two changes in one "
        "iteration make the next profile unattributable"
    )


def test_does_not_fire_when_no_cluster_is_giant():
    assert rule_cluster_giant(_profile(n_rows=1000, cluster_size_max=40), _cfg(),
                              RunHistory()) is None


def test_does_not_fire_on_an_empty_frame():
    """n_rows == 0 is data_empty's business, and 0.1 * 0 would make any cluster
    look giant."""
    assert rule_cluster_giant(_profile(n_rows=0, cluster_size_max=0), _cfg(),
                              RunHistory()) is None


def test_raises_the_threshold_once_splitting_is_already_on():
    """Splitting first because it targets the pathology; raising the threshold
    also drops true pairs, so it is the fallback."""
    cfg = _cfg(cluster=ClusterConfig(split_weak_bridges=True))
    out = rule_cluster_giant(_profile(n_rows=1000, cluster_size_max=400), cfg,
                             RunHistory())
    assert out is not None
    new_cfg, _ = out
    assert new_cfg.matchkeys[0].threshold == pytest.approx(0.75)


def test_stops_at_the_ceiling():
    """At the ceiling there is nothing left to offer, and returning a config
    equal to the input would trip the policy's do-nothing guard."""
    cfg = _cfg(threshold=0.95, cluster=ClusterConfig(split_weak_bridges=True))
    assert rule_cluster_giant(_profile(n_rows=1000, cluster_size_max=400), cfg,
                              RunHistory()) is None


def test_it_declares_the_condition_it_answers():
    from goldenmatch.core.autoconfig_rules import DEFAULT_RULES

    assert rule_cluster_giant.targets == ("cluster_giant",)
    assert rule_cluster_giant in DEFAULT_RULES
