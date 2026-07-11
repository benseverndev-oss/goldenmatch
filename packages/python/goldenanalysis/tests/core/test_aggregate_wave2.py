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


def test_mean_matches_naive_left_to_right_sum():
    # The native kernel sums naively left-to-right (`iter().sum::<f64>()`), and
    # `_mean_pure` mirrors it with an explicit fold. Assert against a naive
    # reference, NOT builtin `sum()`: CPython 3.12 made `sum()` use Neumaier
    # COMPENSATED summation for floats, so on this order-sensitive fixture it
    # recovers the 100 ones (=> 100.0) while the naive kernel cancels them to 0.0.
    # Comparing to `sum()` would therefore fail on 3.12+ and pass on <=3.11 --
    # a version-dependent assertion. The explicit fold is naive on every version.
    v = [1e16] + [1.0] * 100 + [-1e16]
    naive = 0.0
    for x in v:
        naive += x
    assert agg.mean(v) == naive / len(v)


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
