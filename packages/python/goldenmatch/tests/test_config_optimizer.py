"""Tests for the agentic config optimizer (core/config_optimizer.py).

Covers the candidate-generation (threshold sweep), the confidence objective
(zero-label, scored on a sample), and the supervised F1 objective. The search
warm-starts from auto_configure_df by default; explicit base_configs keep the
F1 path deterministic and avoid cross-encoder downloads in offline CI.
"""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.config_optimizer import (
    OptimizeResult,
    _threshold_variants,
    optimize_config,
)


@pytest.fixture(autouse=True)
def _no_cross_run_memory(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")


def _df() -> pl.DataFrame:
    return pl.DataFrame({
        "first_name": ["John", "Jon", "Jane", "Bob", "Bobby"] * 4,
        "last_name": ["Smith", "Smith", "Doe", "Jones", "Jones"] * 4,
        "email": ["j@x.com", "j@x.com", "jane@y.com", "b@z.com", "b@z.com"] * 4,
    })


def _weighted_config(threshold: float = 0.8) -> GoldenMatchConfig:
    return GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="mk", type="weighted", threshold=threshold, rerank=False,
            fields=[MatchkeyField(field="first_name", scorer="jaro_winkler", weight=1.0, transforms=[])],
        )],
        blocking=BlockingConfig(
            strategy="static", keys=[BlockingKeyConfig(fields=["last_name"], transforms=[])],
        ),
    )


def _exact_config() -> GoldenMatchConfig:
    return GoldenMatchConfig(matchkeys=[MatchkeyConfig(
        name="mk", type="exact",
        fields=[MatchkeyField(field="email", scorer="exact", weight=1.0, transforms=[])],
    )])


# --- candidate generation ---

def test_threshold_variants_sweeps_each_offset():
    variants = _threshold_variants(_weighted_config(0.8), (-0.1, -0.05, 0.0, 0.05, 0.1))
    labels = [lbl for lbl, _ in variants]
    assert "baseline" in labels
    thresholds = sorted(round(cfg.get_matchkeys()[0].threshold, 4) for _, cfg in variants)
    assert thresholds == [0.7, 0.75, 0.8, 0.85, 0.9]


def test_threshold_variants_dedups_clamped_collisions():
    # Both +0.1 and +0.2 clamp to 1.0 at threshold 0.95 -> collapse to one.
    variants = _threshold_variants(_weighted_config(0.95), (0.0, 0.1, 0.2))
    thresholds = sorted(round(cfg.get_matchkeys()[0].threshold, 4) for _, cfg in variants)
    assert thresholds == [0.95, 1.0]


def test_threshold_variants_baseline_only_for_exact():
    variants = _threshold_variants(_exact_config(), (-0.1, 0.0, 0.1))
    assert len(variants) == 1
    assert variants[0][0] == "baseline"


# --- confidence objective (default, label-free) ---

def test_optimize_confidence_default_objective():
    result = optimize_config(_df(), base_config=_weighted_config(0.8))
    assert isinstance(result, OptimizeResult)
    assert result.objective == "confidence"
    assert len(result.trials) >= 3
    assert isinstance(result.best_config, GoldenMatchConfig)
    # every confidence trial carries a profile + a score in [0, 1]
    for t in result.trials:
        if t.error is None:
            assert 0.0 <= t.score <= 1.0
            assert t.profile is not None
    # best is among the trials and at least as good as any other
    assert result.best_trial in result.trials
    best_score = result.best_trial.score
    assert all(t.score <= best_score for t in result.trials if t.error is None)


def test_optimize_confidence_warm_starts_from_auto_config():
    # No base_config -> warm start via auto_configure_df, must not raise.
    result = optimize_config(_df())
    assert result.objective == "confidence"
    assert result.best_config.get_matchkeys()


def test_report_mentions_objective_and_best_marker():
    result = optimize_config(_df(), base_config=_weighted_config(0.8))
    text = result.report()
    assert "objective=confidence" in text
    assert "*" in text  # best trial marked


# --- supervised F1 objective ---

def test_optimize_f1_objective_with_ground_truth():
    # Row ids are 0-based by row order; rows 0/1 share an email, 3/4 share one.
    df = pl.DataFrame({
        "first_name": ["John", "Jon", "Jane", "Bob", "Bobby"],
        "last_name": ["Smith", "Smith", "Doe", "Jones", "Jones"],
        "email": ["j@x.com", "j@x.com", "jane@y.com", "b@z.com", "b@z.com"],
    })
    ground_truth = {(0, 1), (3, 4)}
    result = optimize_config(
        df, base_config=_weighted_config(0.8), ground_truth=ground_truth,
    )
    assert result.objective == "f1"
    assert 0.0 <= result.best_trial.score <= 1.0
    assert any("P=" in r for t in result.trials for r in t.reasons)


def test_f1_requires_ground_truth():
    with pytest.raises(ValueError, match="ground_truth"):
        optimize_config(_df(), base_config=_weighted_config(0.8), objective="f1")


def test_invalid_objective_raises():
    with pytest.raises(ValueError, match="objective"):
        optimize_config(_df(), base_config=_weighted_config(0.8), objective="nonsense")


def test_lazyframe_accepted():
    result = optimize_config(_df().lazy(), base_config=_weighted_config(0.8))
    assert isinstance(result, OptimizeResult)
