"""Lever C — answer-candidate-scored path prune for the local synthesis ball.

Lever A (`subgraph_filter.filter_subgraph_to_paths`) was refuted: anchor-to-anchor topology has
no operating point that keeps a single-anchor chain AND shrinks the ball (recall-safe ⟺ ~no
pruning — see `benchmarks/er-kg-bench/results/RESULTS_PATH_AWARE_RETRIEVAL.md`). This prune uses
the QUERY signal Lever A ignored: it scores candidate END nodes by embedding cosine of their
canonical NAME against the question, keeps only the seed→top-`c`-candidate shortest paths (+ a
small `halo`), and derives its pruning power from `top_c ≪ |ball|` rather than a halo radius that
must shrink to prune.

It scores NODES, never edge predicates — so it dodges the 2026-06-22 predicate-focus revert
(real LLM-extracted predicates rarely match the query's relation words verbatim, which dropped
the true chain). Pure Python over the `{entities, edges}` dict + an `Embedder` (`.embed(list[str])
-> ndarray`), so no native/store dependency.

**Recall claim (honest).** "Recall-safe by construction" here means every KEPT candidate lies on
a real seed-rooted path — no stranded topology fragments — NOT that the true answer is guaranteed
kept. The answer survives only if its end-node is among the top-`c` embedding candidates (or
within `halo` of a seed). Whether that holds at a pruning-meaningful `top_c` is what the bench
recall guard measures; it is not asserted here.
"""

from __future__ import annotations

from collections import deque

import numpy as np


def _undirected_adj(edges) -> dict[int, set[int]]:
    adj: dict[int, set[int]] = {}
    for e in edges:
        a, b = e["subj"], e["obj"]
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    return adj


def _reachable_within(adj: dict[int, set[int]], seeds: list[int], k_hops: int) -> set[int]:
    """Nodes within `k_hops` undirected hops of any seed (seeds included)."""
    seen: set[int] = set(seeds)
    frontier: set[int] = set(seeds)
    for _ in range(max(k_hops, 0)):
        nxt: set[int] = set()
        for u in frontier:
            nxt |= adj.get(u, set())
        nxt -= seen
        seen |= nxt
        frontier = nxt
        if not frontier:
            break
    return seen


def _shortest_path(adj: dict[int, set[int]], src: int, dst: int) -> list[int]:
    """BFS shortest path `src`->`dst` over undirected `adj`, ascending-id next-hop tie-break
    (deterministic). Node list inclusive of both ends, or `[]` if unreachable. Mirrors
    `subgraph_filter._shortest_path`."""
    if src == dst:
        return [src]
    prev: dict[int, int | None] = {src: None}
    q: deque[int] = deque([src])
    while q:
        u = q.popleft()
        for v in sorted(adj.get(u, ())):
            if v in prev:
                continue
            prev[v] = u
            if v == dst:
                path = [v]
                while prev[path[-1]] is not None:
                    path.append(prev[path[-1]])  # type: ignore[arg-type]
                return list(reversed(path))
            q.append(v)
    return []


def _nearest_seed_path(adj: dict[int, set[int]], seeds: list[int], dst: int) -> list[int]:
    """Shortest path to `dst` from whichever seed is closest (fewest hops). `[]` if no seed
    reaches it. Seed order breaks length ties (first seed wins — deterministic)."""
    best: list[int] = []
    for s in seeds:
        p = _shortest_path(adj, s, dst)
        if p and (not best or len(p) < len(best)):
            best = p
    return best


def prune_to_candidate_paths(
    subgraph: dict,
    seeds: list[int],
    question: str,
    embedder,
    *,
    k_hops: int = 4,
    top_c: int = 3,
    halo: int = 1,
) -> dict:
    """Prune `subgraph` to the seed→top-`c`-query-candidate paths (+ `halo`-hop seed
    neighbourhood). See the module docstring for the mechanism and the recall caveat.

    No seeds, or no entities -> `subgraph` returned UNCHANGED (nothing to anchor on; mirrors
    `filter_subgraph_to_paths`). Seeds + their `halo` always survive, so the result is never empty
    when the input is non-empty."""
    ents = subgraph.get("entities", [])
    edges = subgraph.get("edges", [])
    seed_ids = list(dict.fromkeys(int(s) for s in seeds))  # dedup, preserve order
    if not seed_ids or not ents:
        return subgraph

    adj = _undirected_adj(edges)
    seed_set = set(seed_ids)
    id_to_name = {int(e["entity_id"]): str(e.get("canonical_name", "")) for e in ents}

    keep: set[int] = set(seed_ids)
    # halo-hop neighbourhood of the seeds (small slack; a lone anchor's answer may be adjacent).
    frontier = set(seed_ids)
    for _ in range(max(halo, 0)):
        nxt: set[int] = set()
        for u in frontier:
            nxt |= adj.get(u, set())
        nxt -= keep
        keep |= nxt
        frontier = nxt
        if not frontier:
            break

    # candidate END nodes: reachable-from-a-seed, non-seed, non-empty name (mirror
    # `seed_by_query`'s empty-name drop — an empty input 400s the provider batch).
    reachable = _reachable_within(adj, seed_ids, k_hops)
    candidates = [
        i
        for i in reachable
        if i not in seed_set and id_to_name.get(i, "").strip()
    ]
    if candidates and top_c > 0:
        names = [id_to_name[i] for i in candidates]
        vecs = np.asarray(embedder.embed([question] + names), dtype=float)
        if vecs.ndim == 2 and vecs.shape[0] == len(names) + 1 and vecs.shape[1] > 0:
            qv = vecs[0]
            mat = vecs[1:]
            qn = qv / (np.linalg.norm(qv) + 1e-12)
            mn = mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-12)
            sims = mn @ qn
            # top-c by descending sim; ascending entity_id tie-break (as seed_by_query)
            order = sorted(range(len(candidates)), key=lambda j: (-float(sims[j]), candidates[j]))
            chosen = [candidates[j] for j in order[:top_c]]
        else:  # degenerate embedder output -> keep all reachable candidates (recall-first)
            chosen = candidates
        for c in chosen:
            path = _nearest_seed_path(adj, seed_ids, c)
            keep.update(path)

    ents2 = [e for e in ents if int(e["entity_id"]) in keep]
    edges2 = [e for e in edges if e["subj"] in keep and e["obj"] in keep]
    return {**subgraph, "entities": ents2, "edges": edges2}
