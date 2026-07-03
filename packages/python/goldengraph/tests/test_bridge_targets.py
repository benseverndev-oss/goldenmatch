"""Case B: fragment the BRIDGE (gold answers' 1-hop neighbors), keep answers intact.
`bridge_targets` picks the neighbor ids to fragment -- deterministic, capped, and
never a gold answer itself."""
from __future__ import annotations

from goldengraph.stark_inject import bridge_targets

# answer A(=1) has neighbors 2,3,4,5; B(=9) has neighbor 3 (shared) and 8. 6 is unrelated.
_EDGES = [("1", "r", "2"), ("3", "r", "1"), ("1", "r", "4"), ("1", "r", "5"),
          ("9", "r", "3"), ("8", "r", "9"), ("6", "r", "7")]


def test_bridge_targets_are_gold_neighbors_excluding_gold():
    t = bridge_targets(_EDGES, {"1", "9"}, cap=10)
    assert t == {"2", "3", "4", "5", "8"}              # neighbors of 1 or 9
    assert "1" not in t and "9" not in t                # gold answers stay intact
    assert "6" not in t and "7" not in t                # unrelated nodes untouched


def test_bridge_targets_capped_per_gold_deterministic():
    # cap=2 per gold: answer 1 keeps its 2 lexicographically-smallest neighbors
    t = bridge_targets(_EDGES, {"1"}, cap=2)
    assert t == {"2", "3"}                              # sorted neighbors of 1, first 2


def test_bridge_targets_shared_neighbor_not_double_counted():
    t = bridge_targets(_EDGES, {"1", "9"}, cap=10)
    # 3 is a neighbor of BOTH 1 and 9 -> appears once
    assert sorted(t) == ["2", "3", "4", "5", "8"]
