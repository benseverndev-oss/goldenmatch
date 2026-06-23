"""NL query orchestration over the durable store + text-to-Cypher export.

`ask` is the "ask the KG" entry point: take an `as_of` slice, seed it (local) or
walk its communities (global), and synthesize. `to_cypher` emits a Cypher string
for Neo4j users (it does NOT execute — no Neo4j dependency; the caller runs it).
"""

from __future__ import annotations

from .embed import Embedder, seed_by_query
from .llm import LLMClient
from .synthesize import synthesize_global, synthesize_local


def _retrieve_local(slice_graph, seeds, *, max_hops: int, node_budget: int) -> dict:
    """Expand the seed neighborhood depth-by-depth up to ``max_hops``, stopping early
    once the subgraph reaches ``node_budget`` entities.

    A single fixed-depth ball (the old ``query(seeds, 1)``) cannot contain the answer
    to a k-hop question -- the answer edge sits at distance k, so for k>=2 it is simply
    absent and synthesis correctly reports "insufficient". Growing the radius keeps
    multi-hop answers reachable; the node budget bounds context + cost so a large graph
    doesn't blow up the prompt.

    (A relation-aware focusing pass over this ball -- pruning to the predicates the
    query named -- was measured WORSE on the QA-e2e bench: real LLM-extracted predicates
    rarely match the query's relation words verbatim, so the focus dropped the true
    chain. Reverted 2026-06-22; the lesson is in the handoff. Precision is now attacked
    on the synthesis side, which cannot strand the answer.)"""
    if not seeds:
        return slice_graph.query(seeds, max_hops)
    sub = slice_graph.query(seeds, 1)
    for h in range(2, max(max_hops, 1) + 1):
        if len(sub.get("entities", ())) >= node_budget:
            break
        sub = slice_graph.query(seeds, h)
    return sub


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
    (bounded by `node_budget` entities) so multi-hop answers are reachable, then
    synthesized with explicit step-by-step relation tracing. `mode="global"`: community
    map-reduce (capped at `max_communities` --
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
    subgraph = _retrieve_local(slice_graph, seeds, max_hops=hops, node_budget=node_budget)
    # Hand the synthesis the seed entity NAMES (the query-relevant anchors) so the
    # multi-hop walk starts at the right place instead of guessing among the ball.
    id_to_name = {
        e["entity_id"]: e["canonical_name"] for e in subgraph.get("entities", ())
    }
    seed_names = [id_to_name[s] for s in seeds if s in id_to_name]
    return synthesize_local(query, subgraph, llm, seed_names=seed_names)


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
