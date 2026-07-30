"""Tests for the pure learning-curve sweep aggregation (SP2 Task 5).

Feeds the ER-matcher training perf gate: defines the slice fractions and
assembles the ``learning_curve`` shape ``perf_report.learning_curve_slope``
consumes (mirrors scripts/er_matcher/test_gpu_tiers.py conventions -- pure
stdlib logic, no torch/network)."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from perf_report import learning_curve_slope  # noqa: E402
from sweep import build_learning_curve, data_fractions, slice_len  # noqa: E402


def test_data_fractions():
    assert data_fractions() == [0.1, 0.25, 0.5, 1.0]


def test_slice_len_rounds_and_floors_at_one():
    assert slice_len(1000, 0.1) == 100
    assert slice_len(3, 0.1) == 1          # floors at 1
    assert slice_len(2844, 0.25) == 711


def test_build_learning_curve_sorts_and_shapes():
    curve = build_learning_curve([(1.0, 0.4), (0.1, 0.7), (0.5, 0.5)])
    assert curve == [{"frac": 0.1, "eval_loss": 0.7},
                     {"frac": 0.5, "eval_loss": 0.5},
                     {"frac": 1.0, "eval_loss": 0.4}]


def test_round_trips_into_slope():
    # descending loss with more data -> positive slope (still improving)
    curve = build_learning_curve([(0.5, 0.5), (1.0, 0.4)])
    assert learning_curve_slope(curve) > 0
