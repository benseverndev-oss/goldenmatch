"""Wheel/caller symbol-skew guard (the #688 secondary-bug class).

Several call sites are gated by ``native_enabled(component)`` on the component's
FLOOR symbol (``_COMPONENT_SYMBOLS``) but then invoke a symbol NEWER than that
floor. On a published wheel that carries the floor symbol but predates the newer
one, the gate fired True and the call raised ``AttributeError`` — a hard crash
instead of the intended silent-slow fallback.

The fix threads the specific symbol through ``native_enabled(component, symbol)``
so the gate is False when the exact symbol is absent, and the call site falls
back to pure Python. These tests simulate that skew with a fake native module
that has each component's floor symbol but NONE of the beyond-floor symbols, and
assert every guarded call site falls back (correct result, no crash).
"""
import types

import pytest

from goldenmatch.core import _native_loader
from goldenmatch.core import cluster as cluster_mod
from goldenmatch.core import pairs as pairs_mod


@pytest.fixture
def floor_only_native(monkeypatch):
    """Fake native module carrying only the FLOOR symbols for ``pairs`` and
    ``clustering`` — none of the beyond-floor symbols the call sites use."""

    def _boom(*_a, **_k):  # floor symbols exist for the gate but must NOT be called
        raise AssertionError("native floor symbol unexpectedly invoked")

    fake = types.SimpleNamespace(
        dedup_pairs_max_score=_boom,  # 'pairs' floor (_COMPONENT_SYMBOLS)
        mst_split_components=_boom,  # 'clustering' floor (NOT connected_components)
    )
    monkeypatch.setattr(_native_loader, "_native", fake)
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "auto")

    # The component gate is ON (floor present) but each specific symbol is absent.
    assert _native_loader.native_enabled("pairs")
    assert not _native_loader.native_enabled("pairs", "candidate_pair_count")
    assert not _native_loader.native_enabled("pairs", "block_histogram")
    assert not _native_loader.native_enabled("pairs", "connected_components")
    assert _native_loader.native_enabled("clustering")
    assert not _native_loader.native_enabled("clustering", "severe_bridge_count")
    assert not _native_loader.native_enabled("clustering", "cluster_confidence")
    return fake


def test_candidate_pair_count_falls_back(floor_only_native):
    # 3*(3-1)//2 + 2*(2-1)//2 = 3 + 1
    assert pairs_mod.candidate_pair_count([3, 2]) == 4


def test_block_histogram_falls_back(floor_only_native):
    h = pairs_mod.block_histogram([1, 2, 3])
    assert h["count"] == 3
    assert h["total_records"] == 6
    assert h["max"] == 3


def test_connected_components_falls_back(floor_only_native):
    comps = pairs_mod.connected_components([(0, 1, 1.0), (1, 2, 1.0)], [0, 1, 2, 3])
    assert sorted(sorted(c) for c in comps) == [[0, 1, 2], [3]]


def test_severe_bridge_count_falls_back(floor_only_native):
    # No crash, returns the pure-Python int (a-b-c chain: the middle edges are
    # bridges to sides of size < 2, so the exact count is not asserted here —
    # the point is fallback, not native-parity, which other tests cover).
    out = cluster_mod._severe_bridge_count([0, 1, 2], {(0, 1): 0.9, (1, 2): 0.9})
    assert isinstance(out, int)


def test_cluster_confidence_falls_back(floor_only_native):
    out = cluster_mod.compute_cluster_confidence({(0, 1): 0.9, (1, 2): 0.8}, 3)
    assert "confidence" in out
    assert out["confidence"] is not None
    assert 0.0 <= out["confidence"] <= 1.0


def test_native_enabled_symbol_present_still_native(monkeypatch):
    """Sanity: when the specific symbol IS present, the gate stays native."""

    def _ok(*_a, **_k):
        return 0

    fake = types.SimpleNamespace(dedup_pairs_max_score=_ok, candidate_pair_count=_ok)
    monkeypatch.setattr(_native_loader, "_native", fake)
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "auto")
    assert _native_loader.native_enabled("pairs", "candidate_pair_count")
