"""Lever C primitive: `prune_to_candidate_paths` — an answer-candidate-scored, recall-safe-by-
construction prune. Pure over the `{entities, edges}` dict + a stub `Embedder` (no network).

Distinction from `filter_subgraph_to_paths` (Lever A): the kept paths lead to the top-`c`
QUERY-relevant end nodes (embedding cosine of candidate NAME vs question), not every
anchor-to-anchor bridge — so the prune target is chosen by the query signal, and pruning power
comes from `top_c`, not from shrinking a halo. Scores NODES, never edge predicates
(dodges the 2026-06-22 predicate-focus revert).
"""
from __future__ import annotations

import numpy as np

from goldengraph.retrieve_paths import prune_to_candidate_paths


class VecEmbedder:
    """Deterministic stub: maps known texts to fixed unit vectors; unknown → zero vector.
    Mirrors the real `Embedder.embed(list[str]) -> np.ndarray` contract."""

    def __init__(self, table: dict[str, list[float]], dim: int = 3):
        self._t = table
        self._dim = dim

    def embed(self, texts: list[str]) -> np.ndarray:
        rows = [self._t.get(t, [0.0] * self._dim) for t in texts]
        return np.asarray(rows, dtype=float)


def _ball():
    # gold chain: 0(seed) -> 1(A) -> 2(ANSWER); off-topic branch: 0 -> 3(X) -> 4(Y)
    return {
        "entities": [
            {"entity_id": 0, "canonical_name": "seed", "typ": "concept"},
            {"entity_id": 1, "canonical_name": "A", "typ": "concept"},
            {"entity_id": 2, "canonical_name": "ANSWER", "typ": "concept"},
            {"entity_id": 3, "canonical_name": "X", "typ": "concept"},
            {"entity_id": 4, "canonical_name": "Y", "typ": "concept"},
        ],
        "edges": [
            {"subj": 0, "predicate": "r", "obj": 1},
            {"subj": 1, "predicate": "r", "obj": 2},
            {"subj": 0, "predicate": "r", "obj": 3},
            {"subj": 3, "predicate": "r", "obj": 4},
        ],
    }


def _ids(sub):
    return sorted(e["entity_id"] for e in sub["entities"])


def test_no_seeds_is_identity():
    sub = _ball()
    emb = VecEmbedder({})
    assert prune_to_candidate_paths(sub, [], "q", emb) is sub


def test_empty_entities_is_identity():
    sub = {"entities": [], "edges": []}
    assert prune_to_candidate_paths(sub, [0], "q", VecEmbedder({})) is sub


def test_seeds_always_kept():
    sub = _ball()
    # embedder scores nothing (all zero) -> no candidate paths, but seeds + halo survive
    out = prune_to_candidate_paths(sub, [0], "q", VecEmbedder({}), top_c=0, halo=0)
    assert 0 in _ids(out)


def test_keeps_chain_to_top_candidate_prunes_offtopic():
    # question aligns with ANSWER; X/Y are orthogonal -> the seed->A->ANSWER chain is kept,
    # the seed->X->Y branch is pruned.
    sub = _ball()
    emb = VecEmbedder(
        {
            "q": [1.0, 0.0, 0.0],
            "ANSWER": [1.0, 0.0, 0.0],
            "A": [0.0, 1.0, 0.0],
            "X": [0.0, 0.0, 1.0],
            "Y": [0.0, 0.0, 1.0],
            "seed": [0.0, 1.0, 0.0],
        }
    )
    out = prune_to_candidate_paths(sub, [0], "q", emb, k_hops=4, top_c=1, halo=0)
    kept = _ids(out)
    assert kept == [0, 1, 2]  # seed + A (bridge) + ANSWER; X(3), Y(4) dropped
    # edges pruned to the kept set
    assert all(e["subj"] in kept and e["obj"] in kept for e in out["edges"])


def test_top_c_over_candidate_count_keeps_all_reachable():
    sub = _ball()
    emb = VecEmbedder({"q": [1.0, 0.0, 0.0]})  # everything ties at 0 sim
    out = prune_to_candidate_paths(sub, [0], "q", emb, k_hops=4, top_c=99, halo=0)
    # top_c exceeds #candidates -> no crash; every reachable node kept
    assert _ids(out) == [0, 1, 2, 3, 4]


def test_every_kept_node_on_a_seed_rooted_path():
    # the by-construction invariant: no stranded fragment — every kept non-seed node is
    # reachable from a seed within the kept edges.
    sub = _ball()
    emb = VecEmbedder({"q": [1.0, 0.0, 0.0], "ANSWER": [1.0, 0.0, 0.0]})
    out = prune_to_candidate_paths(sub, [0], "q", emb, k_hops=4, top_c=2, halo=0)
    adj: dict[int, set[int]] = {}
    for e in out["edges"]:
        adj.setdefault(e["subj"], set()).add(e["obj"])
        adj.setdefault(e["obj"], set()).add(e["subj"])
    reachable = {0}
    frontier = {0}
    while frontier:
        nxt = set()
        for u in frontier:
            nxt |= adj.get(u, set()) - reachable
        reachable |= nxt
        frontier = nxt
    assert set(_ids(out)) <= reachable
