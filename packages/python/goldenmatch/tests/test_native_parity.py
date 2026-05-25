"""Parity tests for the optional goldenmatch._native acceleration kernels.

Each test runs the same operation through the pure-Python path
(GOLDENMATCH_NATIVE=0) and the native path (GOLDENMATCH_NATIVE=1) and asserts
identical output. Skipped when the native extension isn't built.

Native is gated OFF by default (see core/_native_loader.py): these tests force
it on per-call via the env var, which native_enabled() reads each time.
"""
from __future__ import annotations

import pytest
from goldenmatch.core import _native_loader
from goldenmatch.core.cluster import _severe_bridge_count, build_clusters

pytestmark = pytest.mark.skipif(
    not _native_loader.native_available(),
    reason="goldenmatch._native not built",
)

# (pairs, all_ids) fixtures spanning singletons, chains, cliques, bridges,
# and an oversized cluster (exercises the auto-split path, which stays Python).
_PAIR_FIXTURES = [
    ([], [1, 2, 3]),                                              # all singletons
    ([(1, 2, 0.9)], [1, 2, 3]),                                   # one pair + singleton
    ([(1, 2, 0.9), (3, 4, 0.9), (2, 3, 0.8)], [1, 2, 3, 4]),      # bridge-joined
    ([(1, 2, 0.9), (2, 3, 0.85), (1, 3, 0.95)], [1, 2, 3]),       # clique
    ([(i, i + 1, 0.9) for i in range(1, 20)], list(range(1, 21))),  # long chain
    ([(1, 2, 1.0), (2, 3, 1.0), (10, 11, 0.7)], [1, 2, 3, 10, 11, 99]),
]


def _normalize(result: dict) -> set:
    """Membership + size + oversized projection, order-independent."""
    return {
        (frozenset(c["members"]), c["size"], c["oversized"])
        for c in result.values()
    }


@pytest.mark.parametrize("pairs,all_ids", _PAIR_FIXTURES)
def test_build_clusters_membership_parity(monkeypatch, pairs, all_ids):
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "0")
    py = build_clusters(list(pairs), all_ids=list(all_ids))
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "1")
    native = build_clusters(list(pairs), all_ids=list(all_ids))
    assert _normalize(py) == _normalize(native)
    # cluster_quality must match cluster-for-cluster (ids are deterministic).
    assert {cid: c["cluster_quality"] for cid, c in py.items()} == \
           {cid: c["cluster_quality"] for cid, c in native.items()}


_BRIDGE_FIXTURES = [
    ([1, 2, 3, 4], {(1, 2): 0.9, (3, 4): 0.9, (2, 3): 0.8}, 1),    # one severe bridge
    ([1, 2, 3], {(1, 2): 0.9, (2, 3): 0.9, (1, 3): 0.9}, 0),       # clique, none
    ([1, 2, 3], {(1, 2): 0.9, (2, 3): 0.9}, 0),                    # 3-chain, none
    # 6-node chain: removing (2,3)/(3,4)/(4,5) each splits into two >=2 sides.
    ([1, 2, 3, 4, 5, 6], {(1, 2): 0.9, (2, 3): 0.9, (3, 4): 0.7,
                          (4, 5): 0.9, (5, 6): 0.9}, 3),
]


@pytest.mark.parametrize("members,pair_scores,expected", _BRIDGE_FIXTURES)
def test_severe_bridge_count_parity(monkeypatch, members, pair_scores, expected):
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "0")
    py = _severe_bridge_count(members, pair_scores)
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "1")
    native = _severe_bridge_count(members, pair_scores)
    assert py == native == expected


def test_native_off_by_default(monkeypatch):
    # Unset env -> "auto" -> Python (no component gated on yet): default-safe.
    monkeypatch.delenv("GOLDENMATCH_NATIVE", raising=False)
    assert _native_loader.native_enabled("clustering") is False


def test_native_required_mode_uses_native(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "1")
    assert _native_loader.native_enabled("clustering") is True
