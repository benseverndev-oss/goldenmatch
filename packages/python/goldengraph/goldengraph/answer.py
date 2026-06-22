"""NL query orchestration over the durable store + text-to-Cypher export.

`ask` is the "ask the KG" entry point: take an `as_of` slice, seed it (local) or
walk its communities (global), and synthesize. `to_cypher` emits a Cypher string
for Neo4j users (it does NOT execute — no Neo4j dependency; the caller runs it).
"""

from __future__ import annotations

from collections import defaultdict

from .embed import Embedder, seed_by_query
from .llm import LLMClient
from .synthesize import synthesize_global, synthesize_local


def _expand_ball(slice_graph, seeds, *, max_hops: int, node_budget: int) -> dict:
    """Expand the seed neighborhood depth-by-depth up to ``max_hops``, stopping early
    once the subgraph reaches ``node_budget`` entities.

    A single fixed-depth ball (the old ``query(seeds, 1)``) cannot contain the answer
    to a k-hop question -- the answer edge sits at distance k, so for k>=2 it is simply
    absent and synthesis correctly reports "insufficient". Growing the radius keeps
    multi-hop answers reachable; the node budget bounds context + cost so a large graph
    doesn't blow up the prompt."""
    if not seeds:
        return slice_graph.query(seeds, max_hops)
    sub = slice_graph.query(seeds, 1)
    for h in range(2, max(max_hops, 1) + 1):
        if len(sub.get("entities", ())) >= node_budget:
            break
        sub = slice_graph.query(seeds, h)
    return sub


def _query_relations(query: str, edges) -> set:
    """Predicates whose surface phrase (``works_at`` -> "works at") appears verbatim in
    the query -- the relations the asker actually named. The basis for focusing: a
    multi-hop question states the relations to follow, so they pick out the answer chain
    from the surrounding neighborhood."""
    q = query.lower()
    relevant = set()
    for e in edges:
        pred = str(e["predicate"])
        phrase = pred.replace("_", " ").lower()
        if phrase and phrase in q:
            relevant.add(pred)
    return relevant


def _focus_by_relations(full: dict, seeds, relevant: set) -> dict | None:
    """Reduce ``full`` to the subgraph reachable from the seeds along ONLY the relevant
    predicates (undirected reachability; directed edges reported). This strips the
    distractor branches a wide ball drags in -- on a path question it collapses the
    whole-neighborhood blob to the answer chain. Returns None when nothing focuses (so
    the caller keeps the full ball)."""
    edges = [e for e in full.get("edges", ()) if str(e["predicate"]) in relevant]
    if not edges:
        return None
    adj: dict[int, list[int]] = defaultdict(list)
    for e in edges:
        adj[e["subj"]].append(e["obj"])
        adj[e["obj"]].append(e["subj"])
    reached = set(seeds)
    stack = list(seeds)
    while stack:
        u = stack.pop()
        for v in adj.get(u, ()):
            if v not in reached:
                reached.add(v)
                stack.append(v)
    kept_edges = [e for e in edges if e["subj"] in reached and e["obj"] in reached]
    if not kept_edges:
        return None
    by_id = {ent["entity_id"]: ent for ent in full.get("entities", ())}
    kept_ents = [by_id[i] for i in sorted(reached) if i in by_id]
    return {"entities": kept_ents, "edges": kept_edges}


def _retrieve_local(slice_graph, seeds, query: str, *, max_hops: int, node_budget: int) -> dict:
    """Retrieve the local subgraph for synthesis: expand the seed neighborhood (so
    multi-hop answers are reachable) then FOCUS it to the relations the query named (so
    the answer chain isn't lost in distractor branches). Falls back to the full ball
    when the query names no graph relation."""
    full = _expand_ball(slice_graph, seeds, max_hops=max_hops, node_budget=node_budget)
    relevant = _query_relations(query, full.get("edges", ()))
    if relevant:
        focused = _focus_by_relations(full, seeds, relevant)
        if focused is not None:
            return focused
    return full


def ask(
    query: str,
    store,
    *,
    llm: LLMClient,
    embedder: Embedder,
    valid_t: int,
    tx_t: int,
    mode: str = "local",
    k: int = 5,
    hops: int = 4,
    max_communities: int = 10,
    node_budget: int = 64,
) -> str:
    """Answer `query` against `store` as-of `(valid_t, tx_t)`.

    `mode="local"`: embedding-seeded neighborhood, expanded adaptively up to `hops`
    (bounded by `node_budget` entities) so multi-hop answers are reachable, then FOCUSED
    to the relations the query named (distractor branches pruned) before synthesis.
    `mode="global"`: community map-reduce (capped at `max_communities` --
    the pre-emptive guard on the N+1 LLM fan-out; per-call budget is the `LLMClient`'s
    job). Each community is contextualized with its immediate (1-hop) neighborhood.
    """
    slice_graph = store.as_of(valid_t, tx_t)
    if mode == "global":
        communities = slice_graph.communities()[:max_communities]
        views = [slice_graph.query(c["members"], 1) for c in communities]
        return synthesize_global(query, views, llm)
    if mode != "local":
        raise ValueError(f"mode must be 'local' or 'global', got {mode!r}")
    seeds = seed_by_query(slice_graph, query, embedder, k=k)
    subgraph = _retrieve_local(
        slice_graph, seeds, query, max_hops=hops, node_budget=node_budget
    )
    return synthesize_local(query, subgraph, llm)


_CYPHER_PROMPT = (
    "Translate the question into a SINGLE Cypher query for a Neo4j knowledge "
    "graph (nodes are entities with a `name`; edges carry a `predicate`). Output "
    "only the Cypher.\nQuestion: {q}\n{schema}"
)


def to_cypher(query: str, llm: LLMClient, *, schema_hint: str | None = None) -> str:
    """Emit a Cypher query string for `query` (NOT executed — the caller runs it
    against their own Neo4j; goldengraph has no Neo4j dependency)."""
    return llm.complete(
        _CYPHER_PROMPT.format(q=query, schema=schema_hint or "")
    ).strip()
