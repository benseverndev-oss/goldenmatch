"""Wave 2 numeric-reduction pure-path unit tests (box-safe; GOLDENANALYSIS_NATIVE=0)."""

import pytest
from goldenanalysis.core import aggregate as agg


def test_mean_basic():
    assert agg.mean([1.0, 2.0, 3.0]) == 2.0
    assert agg.mean([5.0]) == 5.0


def test_mean_empty_is_zero():
    assert agg.mean([]) == 0.0


def test_mean_filters_none():
    # None dropped, matching the native read_f64 null-drop + _histogram/_quantile_pure.
    assert agg.mean([1.0, None, 3.0]) == 2.0


def test_mean_matches_python_sum_over_same_order():
    v = [1e16] + [1.0] * 100 + [-1e16]
    assert agg.mean(v) == sum(v) / len(v)


def test_min_max_basic():
    assert agg.min([3.0, 1.0, 2.0]) == 1.0
    assert agg.max([3.0, 1.0, 2.0]) == 3.0


def test_min_max_empty_is_zero():
    assert agg.min([]) == 0.0
    assert agg.max([]) == 0.0


def test_min_max_filter_none():
    assert agg.min([3.0, None, 1.0]) == 1.0
    assert agg.max([3.0, None, 1.0]) == 3.0


@pytest.mark.parametrize("xs", [[1.0, 2.0, 3.0], [-3.5, 2.25, 100.0], [0.0, 0.0]])
def test_mean_dispatch_equals_pure(xs):
    # Under GOLDENANALYSIS_NATIVE=0 the public dispatch IS the pure path.
    assert agg.mean(xs) == agg._mean_pure(xs)
    assert agg.min(xs) == agg._min_pure(xs)
    assert agg.max(xs) == agg._max_pure(xs)
