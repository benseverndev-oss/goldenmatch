"""Wave 3 cluster_size_histogram pure-path tests (box-safe; GOLDENANALYSIS_NATIVE=0)."""

from goldenanalysis.core import aggregate as agg


def test_buckets_basic():
    assert agg.cluster_size_histogram([1, 1, 2, 3, 4, 5, 1]) == [3, 1, 1, 2]


def test_buckets_empty():
    assert agg.cluster_size_histogram([]) == [0, 0, 0, 0]


def test_buckets_boundary():
    assert agg.cluster_size_histogram([3, 4]) == [0, 0, 1, 1]
    assert agg.cluster_size_histogram([1, 1, 1]) == [3, 0, 0, 0]


def test_dispatch_equals_pure():
    xs = [1, 1, 2, 2, 2, 3, 7, 9]
    assert agg.cluster_size_histogram(xs) == agg._cluster_size_histogram_pure(xs)
