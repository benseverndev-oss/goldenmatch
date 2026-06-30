"""Surface-bridged retrieval (stage-2-C): the ball unions same-name under-merged siblings as it
expands, so an answer stranded behind a split bridge-entity becomes reachable. Pure (stub graph)."""
from __future__ import annotations

# Reuse the under-merge fixture + stub from the chain-retrieval tests (no tests/__init__.py, so the
# sibling module is importable on the pytest path -- same pattern as `from conftest import ...`).
from goldengraph.answer import _retrieve_local, _retrieve_local_bridged
from test_chain_retrieval import _split_graph, _StubGraph


def _names(ball):
    return {e["canonical_name"] for e in ball["entities"]}


def test_plain_ball_strands_at_under_merge():
    # plain retrieval seeded at A cannot reach C: id1 is a sink, id4 (same name 'B') is a different node
    ball = _retrieve_local(_split_graph(), [0], max_hops=4, node_budget=64)
    assert "C" not in _names(ball)


def test_bridged_ball_crosses_under_merge():
    # per-hop surface bridging unions B(id1)<->B(id4), so part_of->C enters the ball
    ball = _retrieve_local_bridged(_split_graph(), [0], max_hops=4, node_budget=64)
    assert "C" in _names(ball)


def _connected_graph():
    # A -acquired-> B -part_of-> C with a SINGLE 'B' node (no under-merge)
    ents = [{"entity_id": i, "canonical_name": n} for i, n in [(0, "A"), (1, "B"), (2, "C")]]
    edges = [{"subj": 0, "predicate": "acquired", "obj": 1},
             {"subj": 1, "predicate": "part_of", "obj": 2}]
    return _StubGraph(ents, edges)


def test_bridged_reaches_answer_on_connected_graph():
    # no under-merge to bridge -> bridging is a no-op for siblings, but the iteration still reaches C
    # (proves bridging doesn't break / loop on the easy case)
    ball = _retrieve_local_bridged(_connected_graph(), [0], max_hops=4, node_budget=64)
    assert "C" in _names(ball)


def test_node_budget_bounds_expansion():
    # budget=1 breaks AFTER the first hop, BEFORE the bridge hop -> C never enters
    ball = _retrieve_local_bridged(_split_graph(), [0], max_hops=4, node_budget=1)
    assert "C" not in _names(ball)


def test_empty_seeds_falls_back():
    ball = _retrieve_local_bridged(_split_graph(), [], max_hops=4, node_budget=64)
    assert ball["entities"] == [] and ball["edges"] == []
