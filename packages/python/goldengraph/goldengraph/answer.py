"""NL query orchestration over the durable store + text-to-Cypher export.

`ask` is the "ask the KG" entry point: take an `as_of` slice, seed it (local) or
walk its communities (global), and synthesize. `to_cypher` emits a Cypher string
for Neo4j users (it does NOT execute — no Neo4j dependency; the caller runs it).
"""

from __future__ import annotations

from .embed import Embedder, seed_by_query
from .llm import LLMClient
from .synthesize import synthesize_global, synthesize_local


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
    hops: int = 1,
    max_communities: int = 10,
) -> str:
    """Answer `query` against `store` as-of `(valid_t, tx_t)`.

    `mode="local"`: embedding-seeded neighborhood → synthesize. `mode="global"`:
    community map-reduce (capped at `max_communities` — the pre-emptive guard on
    the N+1 LLM fan-out; per-call budget is the `LLMClient`'s job).
    """
    slice_graph = store.as_of(valid_t, tx_t)
    if mode == "global":
        communities = slice_graph.communities()[:max_communities]
        views = [slice_graph.query(c["members"], hops) for c in communities]
        return synthesize_global(query, views, llm)
    if mode != "local":
        raise ValueError(f"mode must be 'local' or 'global', got {mode!r}")
    seeds = seed_by_query(slice_graph, query, embedder, k=k)
    subgraph = slice_graph.query(seeds, hops)
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
