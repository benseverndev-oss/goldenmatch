"""Parity: native build_clusters_native vs the Python orchestration in
core/cluster.py::build_clusters.

The native kernel is a prototype that subsumes the Python loop from
connected_components through compute_cluster_confidence (the v34
attribution's 70-75% of cluster wall). This file locks down the
behavior contract before any wiring lands.

Skipped when the native module isn't built (pure-Python install).
"""
from __future__ import annotations

import pytest

native = pytest.importorskip("goldenmatch._native")
if not hasattr(native, "build_clusters_native"):
    pytest.skip(
        "native module loaded but build_clusters_native not exposed",
        allow_module_level=True,
    )

from goldenmatch.core.cluster import build_clusters as py_build_clusters
from goldenmatch.core.cluster import compute_cluster_confidence


def _canon_pair_scores(d):
    """Canonicalize a pair_scores dict for comparison: sort by key tuple
    (Python tolerates either (a,b) or (b,a) insertion order; we don't
    assert on iteration order, only the set of entries + values)."""
    return sorted(d.items())


def _assert_clusters_equal(py_result, rs_result, *, with_quality_fields=False):
    """Deep-equal on the cluster dict structure, modulo:
      - cluster_id assignments (compare by frozenset(members) -> cluster_id mapping)
      - pair_scores dict iteration order (compare canonicalized)
    """
    assert set(py_result.keys()) == set(rs_result.keys()), (
        f"cluster_id sets differ: py={set(py_result.keys())} "
        f"rs={set(rs_result.keys())}"
    )

    # Map by frozenset of members so we don't depend on cluster_id labelling
    # if both paths converge on the same partitioning. (They should --
    # both sort by min(member) and enumerate from 1 -- but compare by
    # content to be safe.)
    py_by_set = {frozenset(info["members"]): info for info in py_result.values()}
    rs_by_set = {frozenset(info["members"]): info for info in rs_result.values()}
    assert set(py_by_set.keys()) == set(rs_by_set.keys()), (
        "cluster member sets differ"
    )

    for members_set in py_by_set:
        py_info = py_by_set[members_set]
        rs_info = rs_by_set[members_set]

        assert py_info["size"] == rs_info["size"]
        assert py_info["oversized"] == rs_info["oversized"]
        assert sorted(py_info["members"]) == sorted(rs_info["members"])
        assert _canon_pair_scores(py_info["pair_scores"]) == _canon_pair_scores(
            rs_info["pair_scores"]
        )
        # The native kernel subsumes the loop "through compute_cluster_confidence"
        # (its docstring) -- i.e. RAW confidence. It deliberately does NOT apply
        # build_clusters' post-hoc weak-cluster confidence downgrade (a separate
        # Python step that runs after compute_cluster_confidence and only fires
        # for weak clusters). So compare the kernel against the RAW formula, not
        # py_build_clusters' (possibly downgraded) value -- otherwise weak-link
        # inputs falsely diverge (0.494 raw vs 0.3458 downgraded). For non-weak
        # clusters the two are identical, so existing cases are unaffected.
        raw_conf = compute_cluster_confidence(
            dict(rs_info["pair_scores"]), rs_info["size"],
        )["confidence"]
        # abs=1e-5: the kernel accumulates the avg_edge sum in f32, so on large
        # clusters it diverges from Python's f64 sum by a few 1e-6 (same f32
        # precision class as the field-matrix kernels' 1e-4 band). Tight enough
        # to catch a real formula divergence, loose enough for f32 rounding.
        assert rs_info["confidence"] == pytest.approx(raw_conf, abs=1e-5)
        # bottleneck_pair: either ordering valid (Python's min() is "first min wins";
        # the kernel mirrors the same iteration order, but the BOTTLENECK ITSELF
        # has only one canonical answer per cluster).
        assert py_info["bottleneck_pair"] == rs_info["bottleneck_pair"], (
            f"bottleneck mismatch for members {sorted(members_set)}: "
            f"py={py_info['bottleneck_pair']} rs={rs_info['bottleneck_pair']}"
        )

        if with_quality_fields:
            assert py_info.get("cluster_quality") == rs_info.get("cluster_quality")


def _native_then_python_wrap(pairs, all_ids, max_cluster_size=1000):
    """Drive the native kernel directly so we can compare it to Python's
    build_clusters output BEFORE doing the auto_split + quality layering
    Python does on top."""
    return native.build_clusters_native(pairs, all_ids, max_cluster_size)


class TestSimpleShapes:
    def test_two_disjoint_pairs(self):
        pairs = [(0, 1, 0.9), (2, 3, 0.8)]
        all_ids = [0, 1, 2, 3]
        py = py_build_clusters(pairs, all_ids, max_cluster_size=1000, auto_split=False)
        rs = _native_then_python_wrap(pairs, all_ids)
        # py adds cluster_quality after the loop; the kernel doesn't. Compare
        # the pre-quality structural fields only.
        _assert_clusters_equal(py, rs, with_quality_fields=False)

    def test_chain_three_members(self):
        # 0-1-2 chain becomes one cluster.
        pairs = [(0, 1, 0.95), (1, 2, 0.85)]
        all_ids = [0, 1, 2]
        py = py_build_clusters(pairs, all_ids, max_cluster_size=1000, auto_split=False)
        rs = _native_then_python_wrap(pairs, all_ids)
        _assert_clusters_equal(py, rs)

    def test_singleton_ids(self):
        # Nodes 5 and 6 have no edges; both stay as singletons.
        pairs = [(0, 1, 0.9)]
        all_ids = [0, 1, 5, 6]
        py = py_build_clusters(pairs, all_ids, max_cluster_size=1000, auto_split=False)
        rs = _native_then_python_wrap(pairs, all_ids)
        _assert_clusters_equal(py, rs)

    def test_empty_pairs(self):
        pairs = []
        all_ids = [0, 1, 2]
        py = py_build_clusters(pairs, all_ids, max_cluster_size=1000, auto_split=False)
        rs = _native_then_python_wrap(pairs, all_ids)
        _assert_clusters_equal(py, rs)


class TestOversized:
    def test_oversized_flag(self):
        # Fully connected 5-node cluster, max=3 -> oversized.
        pairs = []
        for a in range(5):
            for b in range(a + 1, 5):
                pairs.append((a, b, 0.9))
        all_ids = list(range(5))
        # Use auto_split=False so we compare the pre-split structure.
        py = py_build_clusters(pairs, all_ids, max_cluster_size=3, auto_split=False)
        rs = _native_then_python_wrap(pairs, all_ids, max_cluster_size=3)
        _assert_clusters_equal(py, rs)


class TestConfidenceParity:
    def test_weak_link_chain_confidence(self):
        # Long chain with one very weak link -> bottleneck should match.
        pairs = [(0, 1, 0.99), (1, 2, 0.30), (2, 3, 0.95)]
        all_ids = [0, 1, 2, 3]
        py = py_build_clusters(pairs, all_ids, max_cluster_size=1000, auto_split=False)
        rs = _native_then_python_wrap(pairs, all_ids)
        _assert_clusters_equal(py, rs)

    def test_dense_cluster_high_confidence(self):
        pairs = [(0, 1, 0.95), (0, 2, 0.92), (1, 2, 0.94)]
        all_ids = [0, 1, 2]
        py = py_build_clusters(pairs, all_ids, max_cluster_size=1000, auto_split=False)
        rs = _native_then_python_wrap(pairs, all_ids)
        _assert_clusters_equal(py, rs)


class TestSyntheticScale:
    """Profile target: 10K records, 50K pairs. Verifies the kernel returns
    the right shape on a non-trivial input; perf measured separately via
    scripts/bench_native_cluster_kernel.py."""

    def test_10k_records_50k_pairs(self):
        import random
        random.seed(42)
        n_records = 10_000
        n_pairs = 50_000
        all_ids = list(range(n_records))
        # Generate pairs that produce ~2000 multi-record clusters.
        pairs = []
        for _ in range(n_pairs):
            a = random.randint(0, n_records - 1)
            b = random.randint(0, n_records - 1)
            if a == b:
                continue
            pairs.append((min(a, b), max(a, b), random.random() * 0.4 + 0.6))

        py = py_build_clusters(pairs, all_ids, max_cluster_size=1000, auto_split=False)
        rs = _native_then_python_wrap(pairs, all_ids)
        _assert_clusters_equal(py, rs)
