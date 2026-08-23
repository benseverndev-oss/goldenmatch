"""Cluster-level actions are reachable from config, so a rule can select one (#2717).

Auto-config could observe cluster health, go RED on it, and spend its entire
iteration budget reacting -- with the only knob any rule owned, a matchkey
threshold, which measurably cannot move cluster transitivity in either
direction. Meanwhile `core/transitive_consistency.py` implemented exactly the
missing action (split a cluster held together by a weak transitive bridge) and
was reachable ONLY through `GOLDENMATCH_TRANSITIVE_POSTFLIGHT`, an environment
variable no rule can set.

These tests pin the seam that closes that: a `ClusterConfig` on
`GoldenMatchConfig`, with the env var demoted to an override so every existing
caller keeps working.
"""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.config.schemas import ClusterConfig, GoldenMatchConfig
from goldenmatch.core.transitive_consistency import resolve_split_settings


def test_default_is_off_so_nothing_existing_changes():
    """Default OFF -> no-op -> byte-identical, the contract the env var had."""
    assert ClusterConfig().split_weak_bridges is False
    enabled, _margin = resolve_split_settings(None)
    assert enabled is False


def test_config_can_turn_splitting_on_without_an_env_var():
    """The point of the change: a rule can now select this action."""
    cfg = ClusterConfig(split_weak_bridges=True)
    enabled, margin = resolve_split_settings(cfg)
    assert enabled is True
    assert margin == pytest.approx(0.15), "unset margin keeps the shipped default"


def test_config_carries_the_margin_too():
    cfg = ClusterConfig(split_weak_bridges=True, weak_bridge_margin=0.30)
    enabled, margin = resolve_split_settings(cfg)
    assert enabled is True
    assert margin == pytest.approx(0.30)


def test_env_var_still_turns_it_on_when_no_config_says_otherwise(monkeypatch):
    """Backwards compatibility: the env var was the ONLY way in until now, so
    it must keep working for callers that never touch config."""
    monkeypatch.setenv("GOLDENMATCH_TRANSITIVE_POSTFLIGHT", "1")
    enabled, _ = resolve_split_settings(None)
    assert enabled is True


def test_an_explicit_config_off_beats_the_env_var(monkeypatch):
    """Config is the more specific statement of intent. Without this, a rule
    that decided NOT to split could be overridden by ambient environment."""
    monkeypatch.setenv("GOLDENMATCH_TRANSITIVE_POSTFLIGHT", "1")
    enabled, _ = resolve_split_settings(ClusterConfig(split_weak_bridges=False))
    assert enabled is False


def test_env_margin_applies_when_config_leaves_it_unset(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_TRANSITIVE_WEAK_MARGIN", "0.42")
    _, margin = resolve_split_settings(ClusterConfig(split_weak_bridges=True))
    assert margin == pytest.approx(0.42)


def test_cluster_config_round_trips_on_the_top_level_config():
    cfg = GoldenMatchConfig(cluster=ClusterConfig(split_weak_bridges=True))
    assert cfg.cluster is not None
    assert cfg.cluster.split_weak_bridges is True
    assert GoldenMatchConfig().cluster is None


def test_the_pipeline_honours_config_with_no_env_var_set(monkeypatch):
    """End-to-end through `dedupe_df`: config alone must reach the splitter.

    Chained on purpose -- three records where a-b and b-c match strongly and
    a-c does not, which connected components merges into one cluster.
    """
    monkeypatch.delenv("GOLDENMATCH_TRANSITIVE_POSTFLIGHT", raising=False)
    import goldenmatch.core.transitive_consistency as tc

    calls: list[float | None] = []
    original = tc.materialize_and_split

    def spy(clusters, all_pairs, margin=None):
        calls.append(margin)
        return original(clusters, all_pairs, margin)

    monkeypatch.setattr(tc, "materialize_and_split", spy)

    df = pl.DataFrame({
        "name": [f"alpha beta gamma {i // 2}" for i in range(12)],
        "city": ["springfield"] * 12,
    })
    from goldenmatch import dedupe_df

    dedupe_df(df, exact=["city"],
              config=GoldenMatchConfig(cluster=ClusterConfig(split_weak_bridges=True)))
    assert calls, "config-enabled splitting never reached the splitter"
