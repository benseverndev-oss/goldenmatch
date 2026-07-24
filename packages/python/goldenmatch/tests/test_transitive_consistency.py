"""Transitive-consistency cluster postflight (GOLDENMATCH_TRANSITIVE_POSTFLIGHT).

Splits clusters held together by a single WEAK transitive bridge — an edge whose
removal leaves two >=2-node groups, where the weakest edge sits materially below
the average (two cohesive groups joined by one weak link, the classic false
transitive merge). Reuses the in-house bridge/MST/confidence primitives.

OFF is byte-identical (no-op).
"""
from __future__ import annotations

import pytest

from goldenmatch.core.transitive_consistency import (
    _transitive_postflight_enabled,
    materialize_and_split,
    split_weak_transitive_bridges,
)

FLAG = "GOLDENMATCH_TRANSITIVE_POSTFLIGHT"


@pytest.fixture(autouse=True)
def _clear(monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    monkeypatch.delenv("GOLDENMATCH_TRANSITIVE_WEAK_MARGIN", raising=False)


def test_flag_default_off():
    assert _transitive_postflight_enabled() is False


@pytest.mark.parametrize("truthy", ["1", "true", "on", "yes", "enabled"])
def test_flag_truthy(monkeypatch, truthy):
    monkeypatch.setenv(FLAG, truthy)
    assert _transitive_postflight_enabled() is True


def _two_triangles(bridge_score):
    # {1,2,3} and {4,5,6} each fully connected strong; joined only by edge 3-4.
    s = 0.95
    ps = {(1, 2): s, (1, 3): s, (2, 3): s, (4, 5): s, (4, 6): s, (5, 6): s, (3, 4): bridge_score}
    return {0: {"members": [1, 2, 3, 4, 5, 6], "pair_scores": ps, "size": 6}}


def test_splits_weak_bridge():
    refined, report = split_weak_transitive_bridges(_two_triangles(0.55))
    assert report["clusters_split"] == 1
    groups = sorted(tuple(sorted(c["members"])) for c in refined.values())
    assert groups == [(1, 2, 3), (4, 5, 6)]
    assert all(c["cluster_quality"] == "split_transitive" for c in refined.values())


def test_keeps_strong_bridge():
    # Bridge nearly as strong as the rest -> not a weak link -> no split.
    refined, report = split_weak_transitive_bridges(_two_triangles(0.92))
    assert report["clusters_split"] == 0
    assert len(refined) == 1


def test_margin_controls_sensitivity():
    # A bridge at 0.80 (avg~0.93): default margin 0.15 does NOT split; 0.05 does.
    _, r_default = split_weak_transitive_bridges(_two_triangles(0.80))
    assert r_default["clusters_split"] == 0
    _, r_loose = split_weak_transitive_bridges(_two_triangles(0.80), margin=0.05)
    assert r_loose["clusters_split"] == 1


def test_tiny_and_no_pair_clusters_passthrough():
    refined, report = split_weak_transitive_bridges(
        {0: {"members": [1, 2], "pair_scores": {(1, 2): 0.9}, "size": 2},
         1: {"members": [3, 4, 5, 6], "pair_scores": {}, "size": 4}}
    )
    assert report["clusters_split"] == 0
    assert len(refined) == 2


def test_materialize_from_all_pairs():
    # Cluster dict WITHOUT pair_scores (columnar shape) + global all_pairs.
    clusters = {0: {"members": [1, 2, 3, 4, 5, 6], "pair_scores": {}, "size": 6}}
    all_pairs = [(1, 2, 0.95), (1, 3, 0.95), (2, 3, 0.95),
                 (4, 5, 0.95), (4, 6, 0.95), (5, 6, 0.95),
                 (3, 4, 0.55),  # weak bridge
                 (7, 8, 0.95)]  # cross-cluster noise (7,8 not in cluster) -> ignored
    refined, report = materialize_and_split(clusters, all_pairs)
    assert report["clusters_split"] == 1
    groups = sorted(tuple(sorted(c["members"])) for c in refined.values())
    assert groups == [(1, 2, 3), (4, 5, 6)]
