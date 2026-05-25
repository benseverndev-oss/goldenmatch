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
    BlockingStrategyEdit,
    CoordinateDescentProposer,
    LLMProposer,
    OptimizeResult,
    ScorerSwap,
    ThresholdShift,
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


# --- AI-driven iteration (LLM proposer) ---

def test_grid_default_is_single_round():
    result = optimize_config(_df(), base_config=_weighted_config(0.8))
    assert result.proposer == "grid"
    assert result.rounds == 1


def test_max_trials_caps_search():
    result = optimize_config(_df(), base_config=_weighted_config(0.8), max_trials=2)
    assert len(result.trials) == 2


def test_llm_proposer_iterates_with_injected_diff():
    # Inject a fake LLM that proposes a progressively lower threshold each round,
    # so the search runs multiple rounds with unique candidates (no network).
    calls = {"n": 0}

    def fake_diff(state):
        calls["n"] += 1
        return {"matchkeys": [{"name": "mk", "threshold": round(0.60 - 0.02 * state.round, 4)}]}

    prop = LLMProposer(propose_fn=fake_diff, max_llm_calls=3)
    result = optimize_config(_df(), base_config=_weighted_config(0.8), proposer=prop)
    assert result.proposer == "LLMProposer"
    llm_trials = [t for t in result.trials if t.label.startswith("llm-r")]
    assert len(llm_trials) == 3
    assert calls["n"] >= 3
    assert result.rounds >= 2


def test_llm_proposer_stops_when_diff_none():
    prop = LLMProposer(propose_fn=lambda state: None)
    result = optimize_config(_df(), base_config=_weighted_config(0.8), proposer=prop)
    # Only the grid seed ran; no LLM-driven trials.
    assert all(not t.label.startswith("llm-r") for t in result.trials)
    assert result.rounds == 1


def test_llm_string_without_env_falls_back_to_grid(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_AUTOCONFIG_LLM", raising=False)
    result = optimize_config(_df(), base_config=_weighted_config(0.8), proposer="llm")
    assert result.proposer == "llm"
    assert all(not t.label.startswith("llm-r") for t in result.trials)


def test_unknown_proposer_raises():
    with pytest.raises(ValueError, match="proposer"):
        optimize_config(_df(), base_config=_weighted_config(0.8), proposer="nonsense")


def test_apply_config_diff_shared_helper():
    from goldenmatch.core.autoconfig_policy import apply_config_diff
    cfg = _weighted_config(0.8)
    new = apply_config_diff(cfg, {"matchkeys": [{"name": "mk", "threshold": 0.6}]})
    assert new is not None
    assert new.get_matchkeys()[0].threshold == 0.6
    assert apply_config_diff(cfg, {}) is None  # no-op diff


# --- Phase 2: ConfigEdit vocabulary + coordinate-descent proposer ---

def test_config_edits_apply_and_reject():
    cfg = _weighted_config(0.8)

    shifted = ThresholdShift(-0.1).apply(cfg)
    assert shifted is not None
    assert abs(shifted.get_matchkeys()[0].threshold - 0.7) < 1e-9
    assert ThresholdShift(0.0).apply(cfg) is not None  # baseline is valid

    swapped = ScorerSwap("mk", "first_name", "token_sort").apply(cfg)
    assert swapped is not None
    assert swapped.get_matchkeys()[0].fields[0].scorer == "token_sort"
    # no-op swap (same scorer) -> None
    assert ScorerSwap("mk", "first_name", "jaro_winkler").apply(cfg) is None
    # unknown matchkey name -> no change -> None
    assert ScorerSwap("nope", "first_name", "token_sort").apply(cfg) is None

    bl = BlockingStrategyEdit("multi_pass").apply(cfg)
    assert bl is not None and bl.blocking.strategy == "multi_pass"
    assert BlockingStrategyEdit("static").apply(cfg) is None  # same strategy -> None


def test_coordinate_descent_explores_multiple_lever_families():
    prop = CoordinateDescentProposer(scorers=("token_sort",), blocking_strategies=("multi_pass",))
    result = optimize_config(_df(), base_config=_weighted_config(0.8), proposer=prop)
    assert result.proposer == "CoordinateDescentProposer"
    labels = [t.label for t in result.trials]
    assert any(lbl.startswith("threshold") or lbl == "baseline" for lbl in labels)
    assert any(lbl.startswith("scorer:") for lbl in labels)
    assert any(lbl.startswith("blocking:") for lbl in labels)
    assert result.rounds >= 3
    assert isinstance(result.best_config, GoldenMatchConfig)


def test_coordinate_string_alias():
    result = optimize_config(_df(), base_config=_weighted_config(0.8), proposer="coordinate")
    assert result.proposer == "coordinate"
    assert len(result.trials) > len(_threshold_variants(_weighted_config(0.8), (-0.1, -0.05, 0.0, 0.05, 0.1)))
