"""Unit tests for ControllerBudget.for_dataset(n_rows).

Spec §Design / ControllerBudget.for_dataset:
docs/superpowers/specs/2026-05-16-controller-budget-vs-blocking-discovery-design.md.

Pure function -- no side effects, table-driven, trivially testable.
"""
from __future__ import annotations

from goldenmatch.core.autoconfig_controller import ControllerBudget


def test_for_dataset_below_5k_returns_tight_budget():
    """At <5K, sample_skip_below kicks in (full df) -- max_seconds tight."""
    b = ControllerBudget.for_dataset(100)
    assert b.max_seconds == 15.0
    assert b.sample_size_default == 2000  # default; doesn't matter at this N


def test_for_dataset_10k_returns_historical_defaults():
    """5K-100K: today's defaults (30s / 2K). Preserves 100K bench wall."""
    b = ControllerBudget.for_dataset(10_000)
    assert b.max_seconds == 30.0
    assert b.sample_size_default == 2000


def test_for_dataset_at_100k_boundary_lands_in_new_tier():
    """100K exactly hits the >=100K branch. New tier: sqrt-scaled sample,
    60s budget. Bench gate (Phase 5) is calibrated for this."""
    b = ControllerBudget.for_dataset(100_000)
    assert b.max_seconds == 60.0
    # sqrt(100_000) * 20 = 6324.555..., int() => 6324
    assert b.sample_size_default == 6324


def test_for_dataset_500k_sqrt_scaled():
    """500K: sqrt-scaled sample preserves expected dup-pair signal density."""
    b = ControllerBudget.for_dataset(500_000)
    assert b.max_seconds == 60.0
    # sqrt(500_000) * 20 = 14142.135..., int() => 14142
    assert b.sample_size_default == 14142


def test_for_dataset_at_1m_boundary_caps_sample_at_20k():
    """At 1M exactly, hit the cap branch. sample_size capped at 20K to
    keep sample-iteration cost bounded."""
    b = ControllerBudget.for_dataset(1_000_000)
    assert b.max_seconds == 120.0
    assert b.sample_size_default == 20_000


def test_for_dataset_10m_caps_at_20k():
    """Above 1M, sample stays at the cap (no further growth)."""
    b = ControllerBudget.for_dataset(10_000_000)
    assert b.max_seconds == 120.0
    assert b.sample_size_default == 20_000


def test_for_dataset_returns_new_instance_each_call():
    """Pure function; no shared state across calls."""
    b1 = ControllerBudget.for_dataset(50_000)
    b2 = ControllerBudget.for_dataset(50_000)
    assert b1 is not b2
    assert b1 == b2  # but equal


def test_for_dataset_preserves_other_budget_fields():
    """Only sample_size_default and max_seconds vary by tier; the rest
    (max_iterations, sample_skip_below, converge_epsilon, drift_threshold)
    keep their defaults so the iteration loop's other tuning stays stable."""
    b = ControllerBudget.for_dataset(500_000)
    assert b.max_iterations == 3
    assert b.sample_skip_below == 5000
    assert b.converge_epsilon == 0.05
    assert b.drift_threshold == 0.30
