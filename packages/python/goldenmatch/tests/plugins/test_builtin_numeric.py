"""Tests for predefined numeric-aggregate plugins (#predefined-merge-plugins).

Spec: docs/superpowers/specs/2026-05-22-predefined-merge-plugins-design.md
"""
from __future__ import annotations

import pytest
from goldenmatch.plugins.builtin.numeric import (
    NumericMaxStrategy,
    NumericMeanStrategy,
    NumericMinStrategy,
)

# ---------------------------------------------------------------------------
# numeric_max
# ---------------------------------------------------------------------------


def test_numeric_max_picks_largest():
    val, conf, idx = NumericMaxStrategy().merge([10, 50, 25])
    assert val == 50
    assert conf == 1.0
    assert idx == 1


def test_numeric_max_handles_string_numbers():
    val, conf, idx = NumericMaxStrategy().merge(["10", "50", "25"])
    assert val == "50"
    assert conf == 1.0
    assert idx == 1


def test_numeric_max_ignores_non_numeric():
    val, conf, idx = NumericMaxStrategy().merge(["abc", 100, None, "xyz", 200])
    assert val == 200
    assert idx == 4


def test_numeric_max_all_null_returns_none():
    val, conf = NumericMaxStrategy().merge([None, None, None])
    assert val is None
    assert conf == 0.0


def test_numeric_max_all_non_numeric_returns_none():
    val, conf = NumericMaxStrategy().merge(["abc", "xyz", None])
    assert val is None
    assert conf == 0.0


def test_numeric_max_ties_give_first_index():
    val, conf, idx = NumericMaxStrategy().merge([100, 100, 50])
    assert val == 100
    assert idx == 0
    assert conf == 0.7  # tie


# ---------------------------------------------------------------------------
# numeric_min
# ---------------------------------------------------------------------------


def test_numeric_min_picks_smallest():
    val, conf, idx = NumericMinStrategy().merge([10, 50, 25])
    assert val == 10
    assert conf == 1.0
    assert idx == 0


def test_numeric_min_handles_negatives():
    val, conf, idx = NumericMinStrategy().merge([5, -100, 10])
    assert val == -100
    assert idx == 1


def test_numeric_min_all_null_returns_none():
    val, conf = NumericMinStrategy().merge([None, None])
    assert val is None
    assert conf == 0.0


# ---------------------------------------------------------------------------
# numeric_mean
# ---------------------------------------------------------------------------


def test_numeric_mean_averages():
    val, conf, idx = NumericMeanStrategy().merge([10, 20, 30])
    assert val == 20.0
    assert conf == 1.0
    assert idx == 0  # synthesized value, no real provenance


def test_numeric_mean_ignores_null():
    val, conf, idx = NumericMeanStrategy().merge([10, None, 30, None])
    assert val == 20.0
    # coverage = 2/4
    assert conf == 0.5


def test_numeric_mean_all_null_returns_none():
    val, conf = NumericMeanStrategy().merge([None, None])
    assert val is None
    assert conf == 0.0


def test_numeric_mean_handles_mixed_types():
    val, _conf, _idx = NumericMeanStrategy().merge([10, "20", 30])
    assert val == 20.0


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
