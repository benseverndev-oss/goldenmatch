"""The MST splitters must not depend on ``pair_scores`` insertion order when scores tie.

FS scores are sums of discrete level weights, so equal-score edges are common, and
both splitters cut the FIRST minimum edge of the spanning tree. With edges taken in
``pair_scores`` insertion order, that cut followed the order pairs were scored in --
which follows Python's per-process string hashing. Measured on dblp_scholar
(fs-determinism-probe run 34525109806): the same pair SET reached clustering under
PYTHONHASHSEED 0 and 1 in a different ORDER, the one 232-member oversized cluster was
split differently, and the partition differed (27,125 vs 27,035 predicted pairs).
"""

from __future__ import annotations

import random

from goldenmatch.core.cluster import split_oversized_cluster, split_oversized_cluster_to_size

# A path 0-1-2-3-4-5 whose two weakest edges tie at 0.5.
_PATH = [(0, 1, 0.9), (1, 2, 0.5), (2, 3, 0.9), (3, 4, 0.5), (4, 5, 0.9)]


def _scores(order: list[int]) -> dict[tuple[int, int], float]:
    return {(a, b): s for a, b, s in (_PATH[i] for i in order)}


def _partition(subs: list[dict]) -> list[tuple[int, ...]]:
    return sorted(tuple(sorted(s["members"])) for s in subs)


def test_tied_weakest_edges_split_the_same_way_in_any_insertion_order():
    """KNOWN-POSITIVE: with insertion-order tie-breaking, inserting (1,2) first cut
    {0,1} | {2,3,4,5} and inserting (3,4) first cut {0,1,2,3} | {4,5}."""
    members = list(range(6))
    forward = _partition(split_oversized_cluster(members, _scores([0, 1, 2, 3, 4])))
    backward = _partition(split_oversized_cluster(members, _scores([4, 3, 2, 1, 0])))
    assert forward == backward
    # Ties resolve by endpoint pair: (1, 2) sorts before (3, 4), so it is the cut.
    assert forward == [(0, 1), (2, 3, 4, 5)]


def test_split_to_size_is_independent_of_insertion_order_on_ties(monkeypatch):
    # The one-edge cut this file pins; the level default is in test_cluster_split_level_ties.py.
    monkeypatch.setenv("GOLDENMATCH_CLUSTER_SPLIT_TIES", "single")
    members = list(range(6))
    forward = _partition(split_oversized_cluster_to_size(members, _scores([0, 1, 2, 3, 4]), 4))
    backward = _partition(split_oversized_cluster_to_size(members, _scores([4, 3, 2, 1, 0]), 4))
    assert forward == backward == [(0, 1), (2, 3, 4, 5)]


def test_random_tied_graphs_split_identically_under_shuffled_insertion():
    rng = random.Random(7)
    checked = 0
    for _ in range(40):
        n = rng.randint(6, 14)
        members = list(range(n))
        pairs = {
            (a, b): rng.choice((0.5, 0.7, 0.9))
            for a in range(n)
            for b in range(a + 1, n)
            if rng.random() < 0.35
        }
        if len(pairs) < n:
            continue
        items = list(pairs.items())
        seen = None
        for _ in range(4):
            rng.shuffle(items)
            got = (
                _partition(split_oversized_cluster(members, dict(items))),
                _partition(split_oversized_cluster_to_size(members, dict(items), 3)),
            )
            if seen is None:
                seen = got
            assert got == seen
        checked += 1
    assert checked >= 20, "too few graphs exercised the splitters"
