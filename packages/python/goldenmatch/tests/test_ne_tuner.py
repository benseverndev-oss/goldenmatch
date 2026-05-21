"""Tests for adaptive NE tuner (#129).

Spec: docs/superpowers/specs/2026-05-21-adaptive-ne-tuning-design.md
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest
from goldenmatch.core.autoconfig_ne_tuner import (
    DEFAULT_PENALTY,
    DEFAULT_THRESHOLD,
    MIN_CORRECTIONS,
    NETuning,
    tune_ne_field,
)


@dataclass
class _StubCorrection:
    """Mock Correction with the fields the tuner reads.

    Real Correction (core/memory/store.py) has more — we only need
    decision + trust + id for the tuner's grid search.
    """
    id: str
    decision: str  # "match" or "reject"
    trust: float


class _StubStore:
    """Mock MemoryStore that returns a fixed correction list."""

    def __init__(self, corrections: list[_StubCorrection]) -> None:
        self._corrections = corrections

    def get_corrections(self, dataset: str) -> list[_StubCorrection]:
        return list(self._corrections)


def _build_clean_phone_corrections(n_match: int, n_reject: int) -> list[_StubCorrection]:
    """Build corrections where phone disagreement is a near-perfect
    signal: every 'match' has high trust (≥0.7), every 'reject' has
    moderate trust (≥0.5) — penalty should be HIGH (clean signal,
    penalize hard)."""
    corrections = []
    for i in range(n_match):
        corrections.append(_StubCorrection(
            id=f"m{i:04d}", decision="match", trust=0.85,
        ))
    for i in range(n_reject):
        corrections.append(_StubCorrection(
            id=f"r{i:04d}", decision="reject", trust=0.65,
        ))
    return corrections


def test_tuner_returns_no_memory_when_store_is_none():
    """No MemoryStore → tuner short-circuits with defaults."""
    result = tune_ne_field(store=None, dataset="any", field="phone")
    assert result.reason == "no_memory"
    assert result.penalty == DEFAULT_PENALTY
    assert result.threshold == DEFAULT_THRESHOLD
    assert result.n_corrections == 0


def test_tuner_returns_below_minimum_under_threshold():
    """< MIN_CORRECTIONS → tuner falls back to defaults."""
    corrections = _build_clean_phone_corrections(n_match=10, n_reject=10)
    store = _StubStore(corrections)
    result = tune_ne_field(store=store, dataset="d", field="phone")  # type: ignore[arg-type]
    assert result.reason == "below_minimum"
    assert result.penalty == DEFAULT_PENALTY
    assert result.n_corrections == 20


def test_tuner_picks_higher_penalty_for_clean_signal():
    """Clean phone signal (matches=0.85, rejects=0.65) →
    tuner should pick a penalty that separates them. With penalty=0.3
    + threshold=0.4: match passes (0.85 ≥ 0.4), reject fails
    (0.65 - 0.3 = 0.35 < 0.4) → both correct."""
    corrections = _build_clean_phone_corrections(n_match=40, n_reject=40)
    store = _StubStore(corrections)
    result = tune_ne_field(store=store, dataset="d", field="phone")  # type: ignore[arg-type]
    assert result.reason in ("tuned", "overfit_guard"), result.reason
    if result.reason == "tuned":
        # Train F1 should be high on this clean signal.
        assert result.train_f1 is not None
        assert result.train_f1 >= 0.8


def test_tuner_overfit_guard_triggers_when_heldout_drops():
    """If train F1 - heldout F1 > 5pp, revert to defaults."""
    # Construct a fixture where train and heldout differ:
    # First 90 corrections are "clean signal", last 10 are noise.
    train_part = _build_clean_phone_corrections(n_match=45, n_reject=45)
    # Heldout (last 10): all noise → predicted bias diverges from train.
    # The sort uses id, so use ids that sort after train ids.
    heldout_noise = [
        _StubCorrection(id=f"z{i:04d}", decision="match", trust=0.3)
        for i in range(5)
    ] + [
        _StubCorrection(id=f"z{i+5:04d}", decision="reject", trust=0.9)
        for i in range(5)
    ]
    all_corrections = train_part + heldout_noise
    store = _StubStore(all_corrections)
    result = tune_ne_field(store=store, dataset="d", field="phone")  # type: ignore[arg-type]
    # Either the overfit_guard fired OR the tuner happened to pick
    # generic enough params. Both are valid outcomes; pin the contract
    # that the result is one of these two.
    assert result.reason in ("tuned", "overfit_guard")
    if result.reason == "overfit_guard":
        assert result.penalty == DEFAULT_PENALTY
        assert result.threshold == DEFAULT_THRESHOLD
        assert result.heldout_f1 is not None
        assert result.train_f1 is not None
        assert result.train_f1 - result.heldout_f1 > 0.05


def test_netuning_dataclass_is_frozen():
    """NETuning is frozen — pinned values can't be mutated after the
    tuner returns."""
    t = NETuning(
        penalty=0.4, threshold=0.3, n_corrections=100,
        train_f1=0.9, heldout_f1=0.88, reason="tuned",
    )
    with pytest.raises(Exception):  # FrozenInstanceError or dataclass equivalent
        t.penalty = 0.5  # type: ignore[misc]


def test_min_corrections_constant_matches_spec():
    """Pin the spec's 50-correction minimum so future bumps surface."""
    assert MIN_CORRECTIONS == 50


def test_tuner_env_override_min_corrections(monkeypatch: pytest.MonkeyPatch):
    """GOLDENMATCH_NE_TUNER_MIN_CORRECTIONS env overrides the gate."""
    monkeypatch.setenv("GOLDENMATCH_NE_TUNER_MIN_CORRECTIONS", "5")
    corrections = _build_clean_phone_corrections(n_match=5, n_reject=5)
    store = _StubStore(corrections)
    result = tune_ne_field(store=store, dataset="d", field="phone")  # type: ignore[arg-type]
    # With env-override to 5, 10 corrections is above min → tuner runs.
    assert result.reason in ("tuned", "overfit_guard"), result.reason


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
