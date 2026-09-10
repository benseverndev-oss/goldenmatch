"""``GOLDENMATCH_CLUSTER_SPLIT_TIES=level``: the to-size splitter cuts every tied weakest edge.

The default cuts one tied weakest edge, the first in canonical endpoint order (#2935).
That is deterministic but still an arbitrary choice among equals. A level cut is
single-linkage at that height, so its components cannot depend on which tied edge sorts
first or on which equal-weight spanning tree was built.
"""

from __future__ import annotations

import random

from goldenmatch.core.cluster import split_oversized_cluster_to_size

# A path 0-1-2-3-4-5 whose two weakest edges tie at 0.5.
_PATH = [(0, 1, 0.9), (1, 2, 0.5), (2, 3, 0.9), (3, 4, 0.5), (4, 5, 0.9)]


def _scores(edges, order=None) -> dict[tuple[int, int], float]:
    order = range(len(edges)) if order is None else order
    return {(edges[i][0], edges[i][1]): edges[i][2] for i in order}


def _partition(subs: list[dict]) -> list[tuple[int, ...]]:
    return sorted(tuple(sorted(s["members"])) for s in subs)


def test_default_cuts_one_tied_edge(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_CLUSTER_SPLIT_TIES", raising=False)
    got = _partition(split_oversized_cluster_to_size(list(range(6)), _scores(_PATH), 4))
    assert got == [(0, 1), (2, 3, 4, 5)]


def test_level_cuts_every_tied_weakest_edge(monkeypatch):
    """KNOWN-POSITIVE: the default leaves {2,3,4,5}; the level cut also removes (3,4)."""
    monkeypatch.setenv("GOLDENMATCH_CLUSTER_SPLIT_TIES", "level")
    for order in ([0, 1, 2, 3, 4], [4, 3, 2, 1, 0]):
        got = _partition(split_oversized_cluster_to_size(list(range(6)), _scores(_PATH, order), 4))
        assert got == [(0, 1), (2, 3), (4, 5)]


def test_level_does_not_shatter_an_all_tied_component(monkeypatch):
    """Six exact duplicates all scoring 1.0: a level cut would leave six singletons."""
    clique = [(a, b, 1.0) for a in range(6) for b in range(a + 1, 6)]
    monkeypatch.setenv("GOLDENMATCH_CLUSTER_SPLIT_TIES", "level")
    level = _partition(split_oversized_cluster_to_size(list(range(6)), _scores(clique), 4))
    monkeypatch.delenv("GOLDENMATCH_CLUSTER_SPLIT_TIES")
    default = _partition(split_oversized_cluster_to_size(list(range(6)), _scores(clique), 4))
    assert level == default
    assert len(level) < 6


def test_level_partition_is_independent_of_insertion_order(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_CLUSTER_SPLIT_TIES", "level")
    rng = random.Random(11)
    checked = 0
    for _ in range(40):
        n = rng.randint(6, 14)
        edges = [
            (a, b, rng.choice((0.5, 0.7, 0.9)))
            for a in range(n)
            for b in range(a + 1, n)
            if rng.random() < 0.35
        ]
        if len(edges) < n:
            continue
        seen = None
        for _ in range(4):
            rng.shuffle(edges)
            got = _partition(split_oversized_cluster_to_size(list(range(n)), _scores(edges), 3))
            if seen is None:
                seen = got
            assert got == seen
        checked += 1
    assert checked >= 20, "too few graphs exercised the splitter"
