"""Zero-label Phase 2: perturbation-stability controller integration.

Env-gated (GOLDENMATCH_AUTOCONFIG_ZERO_LABEL_STABILITY): when on, the committed
profile's zero_label.perturbation_stability is computed from threshold-perturbed
sample re-runs; when off it stays None (no extra compute).
"""
from __future__ import annotations

import goldenmatch
import polars as pl
import pytest
from goldenmatch.core import autoconfig


@pytest.fixture(autouse=True)
def _no_cross_run_memory(monkeypatch):
    # Deterministic committed config: disable cross-run autoconfig memory.
    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")


def _df() -> pl.DataFrame:
    return pl.DataFrame({
        "first_name": ["John", "Jon", "Jane", "Bob", "Bobby"] * 4,
        "last_name": ["Smith", "Smith", "Doe", "Jones", "Jones"] * 4,
        "email": ["j@x.com", "j@x.com", "jane@y.com", "b@z.com", "b@z.com"] * 4,
    })


def _committed_zero_label():
    last = autoconfig._LAST_CONTROLLER_RUN.get()
    assert last is not None, "controller did not run"
    profile, _history = last
    assert profile.zero_label is not None, "zero_label not attached"
    return profile.zero_label


def test_stability_none_when_flag_off(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_AUTOCONFIG_ZERO_LABEL_STABILITY", raising=False)
    goldenmatch.auto_configure_df(_df(), confidence_required=False)
    assert _committed_zero_label().perturbation_stability is None


def test_stability_computed_when_flag_on(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_ZERO_LABEL_STABILITY", "1")
    goldenmatch.auto_configure_df(_df(), confidence_required=False)
    stab = _committed_zero_label().perturbation_stability
    # Committed config has a weighted (name->ensemble) matchkey -> perturbable.
    assert isinstance(stab, float)
    assert 0.0 <= stab <= 1.0
