"""Contract/parity tests for ``compute_cluster_confidence`` after the
native-path edge-list guard removal.

The native branch used to filter ``pair_scores`` items with
``if isinstance(k, tuple) and len(k) == 2`` on EVERY pair before handing the
edge list to the Rust kernel. ``pair_scores`` keys are always canonical
``(min, max)`` 2-tuples by construction (every writer in ``core/cluster.py``
builds them that way), so the filter re-confirmed a guaranteed invariant and
dropped nothing -- ~132M isinstance + ~132M len calls at 1M / 131M pairs
(~19s, 18% of the cluster stage on the profiled 1M run). The guard was removed;
these tests pin the exact confidence contract so the removal is provably
output-preserving on valid (canonical-key) input.

confidence = 0.4*min_edge + 0.3*avg_edge + 0.3*connectivity
connectivity = len(pair_scores) / (size*(size-1)/2)

Path-independent: the native kernel and the pure-Python fallback are
parity-validated to the same numbers, so these assertions hold whichever path
``native_enabled("clustering")`` selects in the test env.
"""
from __future__ import annotations

import pytest
from goldenmatch.core.cluster import compute_cluster_confidence

_ABS = 1e-9


def test_triangle_full_connectivity():
    """3 members, all 3 edges present -> connectivity 1.0."""
    ps = {(0, 1): 0.9, (0, 2): 0.8, (1, 2): 0.7}
    out = compute_cluster_confidence(ps, size=3)
    assert out["min_edge"] == pytest.approx(0.7, abs=_ABS)
    assert out["avg_edge"] == pytest.approx(0.8, abs=_ABS)
    assert out["connectivity"] == pytest.approx(1.0, abs=_ABS)
    assert out["bottleneck_pair"] == (1, 2)
    # 0.4*0.7 + 0.3*0.8 + 0.3*1.0 = 0.82
    assert out["confidence"] == pytest.approx(0.82, abs=_ABS)


def test_chain_partial_connectivity():
    """3 members, only 2 of 3 possible edges -> connectivity 2/3."""
    ps = {(0, 1): 0.9, (1, 2): 0.6}
    out = compute_cluster_confidence(ps, size=3)
    assert out["min_edge"] == pytest.approx(0.6, abs=_ABS)
    assert out["avg_edge"] == pytest.approx(0.75, abs=_ABS)
    assert out["connectivity"] == pytest.approx(2.0 / 3.0, abs=_ABS)
    assert out["bottleneck_pair"] == (1, 2)
    # 0.4*0.6 + 0.3*0.75 + 0.3*(2/3) = 0.665
    assert out["confidence"] == pytest.approx(0.665, abs=_ABS)


def test_two_member():
    ps = {(0, 1): 0.85}
    out = compute_cluster_confidence(ps, size=2)
    assert out["min_edge"] == pytest.approx(0.85, abs=_ABS)
    assert out["avg_edge"] == pytest.approx(0.85, abs=_ABS)
    assert out["connectivity"] == pytest.approx(1.0, abs=_ABS)
    assert out["bottleneck_pair"] == (0, 1)
    # 0.4*0.85 + 0.3*0.85 + 0.3*1.0 = 0.895
    assert out["confidence"] == pytest.approx(0.895, abs=_ABS)


def test_singleton():
    out = compute_cluster_confidence({}, size=1)
    assert out["connectivity"] == pytest.approx(1.0, abs=_ABS)
    assert out["confidence"] == pytest.approx(1.0, abs=_ABS)
    assert out["bottleneck_pair"] is None


def test_empty_pairs_multi_member():
    """size>1 with no edges -> connectivity 0, confidence 0."""
    out = compute_cluster_confidence({}, size=2)
    assert out["connectivity"] == pytest.approx(0.0, abs=_ABS)
    assert out["confidence"] == pytest.approx(0.0, abs=_ABS)
    assert out["bottleneck_pair"] is None


def test_bottleneck_is_first_minimum():
    """Ties resolve to the first minimum key in dict-insertion order (the
    contract the native + Python paths share)."""
    ps = {(0, 1): 0.5, (0, 2): 0.5, (1, 2): 0.9}
    out = compute_cluster_confidence(ps, size=3)
    assert out["min_edge"] == pytest.approx(0.5, abs=_ABS)
    assert out["bottleneck_pair"] == (0, 1)


def test_larger_cluster_matches_formula():
    """Exercise the comprehension path over many pairs and check the result
    against the formula computed independently."""
    n = 60
    ps = {(i, j): 0.6 + ((i + j) % 5) * 0.05
          for i in range(n) for j in range(i + 1, n)}
    out = compute_cluster_confidence(ps, size=n)
    scores = list(ps.values())
    exp_min = min(scores)
    exp_avg = sum(scores) / len(scores)
    exp_conn = len(ps) / (n * (n - 1) / 2)  # fully connected -> 1.0
    assert out["min_edge"] == pytest.approx(exp_min, abs=_ABS)
    assert out["avg_edge"] == pytest.approx(exp_avg, abs=_ABS)
    assert out["connectivity"] == pytest.approx(exp_conn, abs=_ABS)
    assert out["confidence"] == pytest.approx(
        0.4 * exp_min + 0.3 * exp_avg + 0.3 * exp_conn, abs=_ABS,
    )
