"""P3: an OLD native wheel must not silently reintroduce the f32 decision flips.

`sail_scoring` left `_FALLBACK_ONLY` once goldenmatch-native 0.1.21 shipped the
f64 kernel. But the loader gates on SYMBOL PRESENCE, and
``score_field_pairwise`` exists on the f32 <= 0.1.20 wheels too -- so the loader
alone cannot tell them apart. The call site checks the returned DTYPE instead.

Without that guard, a user on an older installed wheel gets native f32 scoring
under ``auto`` and the exact flips section 6 was written to prevent
(a pure 0.95 arriving as 0.949999988, so ``>= 0.95`` turns over).

No native kernel or Spark needed: the wheel is faked.
"""
from __future__ import annotations

import numpy as np
import pytest
from goldenmatch.sail import scorers

_A = ["Jonathan", "Anderson"]
_B = ["Jonothan", "Andersen"]


class _FakeWheel:
    """Stands in for goldenmatch-native, returning a chosen float width."""

    def __init__(self, dtype):
        self._dtype = dtype
        self.calls = 0

    def score_field_pairwise(self, a, b, scorer_id):  # noqa: ARG002
        self.calls += 1
        return np.array([0.95, 0.95], dtype=self._dtype)


def _install(monkeypatch, wheel):
    monkeypatch.setattr(scorers, "logger", scorers.logger)
    monkeypatch.setattr(scorers, "_NATIVE_SCORER_F32_WARNED", False, raising=False)
    import goldenmatch.core._native_loader as loader

    monkeypatch.setattr(loader, "native_module", lambda: wheel)
    monkeypatch.setattr(loader, "native_enabled", lambda *a, **k: True)


def test_f32_wheel_is_refused_and_falls_back(monkeypatch):
    """The old wheel's answer is DISCARDED, not used."""
    wheel = _FakeWheel(np.float32)
    _install(monkeypatch, wheel)

    out = scorers._native_scores("jaro_winkler", _A, _B)

    assert wheel.calls == 1, "the kernel should be called before the width is known"
    assert out is None, (
        "an f32 wheel must fall back to the pure floor; returning its result "
        "reintroduces the threshold flips that reversed spec section 6"
    )


def test_f64_wheel_is_used(monkeypatch):
    """And the guard must not reject the CORRECT wheel -- a guard that refuses
    everything would 'pass' while disabling the feature entirely."""
    wheel = _FakeWheel(np.float64)
    _install(monkeypatch, wheel)

    out = scorers._native_scores("jaro_winkler", _A, _B)

    assert out is not None, "the f64 wheel must be used"
    assert np.asarray(out).dtype == np.float64


def test_score_batch_still_returns_correct_values_on_an_old_wheel(monkeypatch):
    """End to end: an f32 wheel degrades to the pure floor, so callers still get
    CORRECT scores -- just without the native speedup."""
    _install(monkeypatch, _FakeWheel(np.float32))

    got = np.asarray(scorers.score_batch("jaro_winkler", _A, _B), dtype=np.float64)
    want = np.asarray(scorers._pure_scores("jaro_winkler", _A, _B), dtype=np.float64)

    assert np.array_equal(got, want), "fallback must equal the pure floor exactly"


def test_sail_scoring_is_no_longer_fallback_only():
    """The gate is lifted, and the mechanism is retained for future divergences."""
    from goldenmatch.core._native_loader import _COMPONENT_SYMBOLS, _FALLBACK_ONLY

    assert "sail_scoring" not in _FALLBACK_ONLY
    assert "sail_scoring" in _COMPONENT_SYMBOLS
    assert "score_field_pairwise" in _COMPONENT_SYMBOLS["sail_scoring"]


@pytest.mark.parametrize("width", [np.float16, np.float32])
def test_any_narrow_float_is_refused(width, monkeypatch):
    """Guard on width, not on an f32 literal -- a future wheel returning f16
    would be just as wrong."""
    _install(monkeypatch, _FakeWheel(width))
    assert scorers._native_scores("jaro_winkler", _A, _B) is None
