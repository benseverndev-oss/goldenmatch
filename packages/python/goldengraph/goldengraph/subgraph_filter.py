"""Path-preserving relevance filter for the hybrid synthesis subgraph.

The bench measured hybrid (passages + the FULL seeded ball) BELOW passages-alone:
the off-topic leaves the wide ball drags in dilute the gold passages in the prompt.
This filter removes that dilution WITHOUT stranding the answer chain -- the failure
mode of the 2026-06-22 topology-blind predicate-focus revert (see the
`_retrieve_local` docstring in answer.py). It keeps only:

  * the seed (anchor) entities,
  * every entity on a shortest path between two anchors (the bridges -- by
    construction, so a multi-hop chain is never cut), and
  * each anchor's `halo`-hop neighbourhood (a single anchor's answer is usually a
    direct neighbour, not a node between two anchors).

Edges are treated as UNDIRECTED because hybrid synthesis follows them either way.
Pure Python over the `{entities, edges}` dict -- no native/store dependency, so the
hybrid experiment stays wheel-free.
"""

from __future__ import annotations

from collections import deque


def _shortest_path(adj: dict[int, set[int]], src: int, dst: int) -> list[int]:
    """BFS shortest path `src`->`dst` over undirected `adj`. Neighbours are
    explored in ascending id order, so when several shortest paths exist the one
    via the lowest-id next hop wins -- deterministic. Returns the node list
    (inclusive of both ends), or `[]` if `dst` is unreachable from `src`."""
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


def filter_subgraph_to_paths(
    subgraph: dict, seeds: list[int], *, halo: int = 1
) -> dict:
    """Prune `subgraph` (a `{entities, edges}` dict) to the chain-relevant core for
    the given anchor `seeds`. See module docstring for the kept-set definition.

    No seeds, or an empty entity list -> the subgraph is returned UNCHANGED (there
    is nothing to anchor a filter on; mirrors `_retrieve_local`'s `if not seeds`).
    The result is never empty when the input is non-empty: seeds + halo always
    survive."""
    ents = subgraph.get("entities", [])
    edges = subgraph.get("edges", [])
    seed_ids = list(dict.fromkeys(seeds))  # dedup, preserve order, deterministic
    if not seed_ids or not ents:
        return subgraph

    adj: dict[int, set[int]] = {}
    for e in edges:
        a, b = e["subj"], e["obj"]
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)

    keep: set[int] = set(seed_ids)
    # anchor-to-anchor shortest paths -- the bridges of the multi-hop chain.
    for i in range(len(seed_ids)):
        for j in range(i + 1, len(seed_ids)):
            keep.update(_shortest_path(adj, seed_ids[i], seed_ids[j]))
    # halo-hop neighbourhood of each seed (a lone anchor's answer is often a
    # direct neighbour, on no anchor-to-anchor path).
    for s in seed_ids:
        frontier = {s}
        for _ in range(max(halo, 0)):
            nxt: set[int] = set()
            for u in frontier:
                nxt |= adj.get(u, set())
            nxt -= keep
            keep |= nxt
            frontier = nxt
            if not frontier:
                break

    ents2 = [e for e in ents if e["entity_id"] in keep]
    edges2 = [e for e in edges if e["subj"] in keep and e["obj"] in keep]
    return {**subgraph, "entities": ents2, "edges": edges2}


def rerank_subgraph_edges(
    subgraph: dict, seeds, *, question: str, embedder, top_k: int
) -> dict:
    """Prune `subgraph`'s edges to the top-`top_k` most question-relevant, so a huge,
    noisy hybrid ball (~1,500-2,500 edges, serialized whole into the synthesis prompt by
    `synthesize._format_subgraph`) stops burying the answer edge among distractors.

    Scoring: cosine similarity between the embedding of `question` and the embedding of
    each edge's serialized text -- the SAME name-keyed form the model reads,
    ``"{subj_name} {predicate} {obj_name}"`` (names from the subgraph's entities, falling
    back to the raw id when a name is missing). Batched: ONE `embedder.embed` call for all
    edge texts + one for the question (no per-edge round-trips), mirroring `seed_by_query`.

    Anchor preservation: every edge incident to a `seeds` entity id is ALWAYS retained
    (connectivity), regardless of score; the remaining budget (`top_k` minus the retained
    seed-incident edges) is filled with the highest-scoring non-seed-incident edges (ties
    broken by original edge order, deterministic).

    `top_k >= len(edges)` -> the subgraph is returned UNCHANGED (same object; no-op). The
    returned subgraph is a NEW dict: `edges` pruned to the kept set (original order
    preserved) and `entities` reduced to those referenced by the kept edges UNION the seed
    ids (a seed entity is never dropped). All other keys are preserved unchanged. The input
    is never mutated."""
    import numpy as np

    edges = subgraph.get("edges", [])
    ents = subgraph.get("entities", [])
    if top_k >= len(edges):
        return subgraph

    id_to_name = {e["entity_id"]: e["canonical_name"] for e in ents}
    seed_set = set(seeds)

    def _edge_text(e) -> str:
        subj = id_to_name.get(e["subj"], e["subj"])
        obj = id_to_name.get(e["obj"], e["obj"])
        return f"{subj} {e['predicate']} {obj}"

    seed_incident = [
        i for i, e in enumerate(edges) if e["subj"] in seed_set or e["obj"] in seed_set
    ]
    non_seed = [i for i, e in enumerate(edges) if i not in set(seed_incident)]

    budget = top_k - len(seed_incident)
    selected: list[int] = []
    if budget > 0 and non_seed:
        texts = [_edge_text(edges[i]) for i in non_seed]
        vecs = np.asarray(embedder.embed([question] + texts), dtype=float)
        q = vecs[0]
        mat = vecs[1:]
        qn = q / (np.linalg.norm(q) + 1e-12)
        mn = mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-12)
        sims = mn @ qn
        # highest score first; ties broken by original edge index (deterministic).
        ranked = sorted(range(len(non_seed)), key=lambda j: (-float(sims[j]), non_seed[j]))
        selected = [non_seed[j] for j in ranked[:budget]]

    kept_idx = sorted(set(seed_incident) | set(selected))
    kept_edges = [edges[i] for i in kept_idx]

    referenced = set(seed_set)
    for e in kept_edges:
        referenced.add(e["subj"])
        referenced.add(e["obj"])
    kept_ents = [e for e in ents if e["entity_id"] in referenced]
    return {**subgraph, "entities": kept_ents, "edges": kept_edges}
