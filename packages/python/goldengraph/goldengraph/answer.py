"""NL query orchestration over the durable store + text-to-Cypher export.

`ask` is the "ask the KG" entry point: take an `as_of` slice, seed it (local) or
walk its communities (global), and synthesize. `to_cypher` emits a Cypher string
for Neo4j users (it does NOT execute — no Neo4j dependency; the caller runs it).
"""

from __future__ import annotations

from .embed import Embedder, seed_by_query
from .llm import LLMClient
from .route import plan_query, resolve_profile
from .synthesize import synthesize_global, synthesize_hybrid, synthesize_local


def aggregate_members(slice_graph, anchor_surface: str, relation: str) -> set[str]:
    """Engine-native exact aggregation: seed the anchor by name, 1-hop ball, return the canonical
    NAMES of objects on edges (subj in seeds, predicate==relation). LLM-FREE."""
    seeds = slice_graph.seeds_by_name(anchor_surface)
    if not seeds:
        return set()
    sub = slice_graph.query(seeds, 1)
    id_to_name = {e["entity_id"]: e["canonical_name"] for e in sub.get("entities", ())}
    seedset = set(seeds)
    return {
        id_to_name[e["obj"]]
        for e in sub.get("edges", ())
        if e["subj"] in seedset and e["predicate"] == relation and e["obj"] in id_to_name
    }


def asof_object(slice_graph, anchor_surface: str, relation: str) -> str | None:
    """The object on a (subj==seed, predicate==relation) edge present IN THIS SLICE (the slice
    already encodes the as-of window). The aggregate traversal returning ONE object. LLM-free."""
    seeds = slice_graph.seeds_by_name(anchor_surface)
    if not seeds:
        return None
    sub = slice_graph.query(seeds, 1)
    id_to_name = {e["entity_id"]: e["canonical_name"] for e in sub.get("entities", ())}
    seedset = set(seeds)
    objs = {
        id_to_name[e["obj"]]
        for e in sub.get("edges", ())
        if e["subj"] in seedset and e["predicate"] == relation and e["obj"] in id_to_name
    }
    objs.discard(anchor_surface)
    return next(iter(sorted(objs)), None)


def _format_aggregate(members: set[str]) -> str:
    return ", ".join(sorted(members)) if members else "(none found)"


def _slice_predicates(slice_graph) -> set[str]:
    """Distinct edge predicates in the slice -- the relation vocabulary for slot extraction."""
    ids = [e["entity_id"] for e in slice_graph.entities()]
    if not ids:
        return set()
    return {e["predicate"] for e in slice_graph.query(ids, 1).get("edges", ())}


def _hybrid_filter_mode() -> str:
    """Hybrid subgraph filter selector, read at call time. "" / "none" / unset =
    off (pass the full ball; the measured 0.420 control). "path" = path-preserving
    prune (`subgraph_filter.filter_subgraph_to_paths`)."""
    import os

    return os.environ.get("GOLDENGRAPH_HYBRID_FILTER", "").strip().lower()


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
    passages: object | None = None,
    passage_k: int = 10,
    query_classifier: object | None = None,
) -> str:
    """Answer `query` against `store` as-of `(valid_t, tx_t)`.

    `mode="local"`: embedding-seeded neighborhood, expanded adaptively up to `hops`
    (bounded by `node_budget` entities) so multi-hop answers are reachable, then
    synthesized with explicit step-by-step relation tracing. `mode="hybrid"`: the same
    seeded ball PLUS raw source passages retrieved by `passages.retrieve(query,
    passage_k)` -> `list[str]`, handed to synthesis as the ground-truth context with
    the graph as a cross-passage multi-hop map (recovers the source-text fidelity the
    extracted triples drop; the answer is freed from the entity-only constraint). With
    no `passages` retriever it degrades to passages-empty (graph-only, free-form
    answer). `mode="global"`: community map-reduce (capped at `max_communities` --
    the pre-emptive guard on the N+1 LLM fan-out; per-call budget is the `LLMClient`'s
    job). Each community is contextualized with its immediate (1-hop) neighborhood.
    """
    slice_graph = store.as_of(valid_t, tx_t)
    if mode == "auto":
        profile = resolve_profile(
            query, predicates=_slice_predicates(slice_graph), llm_classifier=query_classifier
        )
        plan = plan_query(profile)
        if plan.mode == "aggregate" and profile.anchor_surface and profile.relation:
            return _format_aggregate(
                aggregate_members(slice_graph, profile.anchor_surface, profile.relation)
            )
        if plan.mode == "as_of" and profile.anchor_surface and profile.relation and profile.as_of:
            # the date IS the slice time -> override the caller's valid_t for this temporal query
            try:
                d = int(profile.as_of)
            except ValueError:
                d = None
            if d is not None:
                obj = asof_object(store.as_of(d, tx_t), profile.anchor_surface, profile.relation)
                return obj if obj is not None else "(unknown)"
        # clamp: a specialized plan that did NOT return must not carry an invalid mode into the
        # `if mode not in ("local","hybrid"): raise` guard below
        mode = plan.mode if plan.mode in ("local", "hybrid", "global") else "local"
    if mode == "global":
        communities = slice_graph.communities()[:max_communities]
        views = [slice_graph.query(c["members"], 1) for c in communities]
        return synthesize_global(query, views, llm)
    if mode not in ("local", "hybrid"):
        raise ValueError(f"mode must be 'local', 'hybrid', or 'global', got {mode!r}")
    seeds = seed_by_query(slice_graph, query, embedder, k=k)
    subgraph = _retrieve_local(slice_graph, seeds, max_hops=hops, node_budget=node_budget)
    # Hand the synthesis the seed entity NAMES (the query-relevant anchors) so the
    # multi-hop walk starts at the right place instead of guessing among the ball.
    id_to_name = {
        e["entity_id"]: e["canonical_name"] for e in subgraph.get("entities", ())
    }
    seed_names = [id_to_name[s] for s in seeds if s in id_to_name]
    if mode == "hybrid":
        if _hybrid_filter_mode() == "path":
            from .subgraph_filter import filter_subgraph_to_paths

            subgraph = filter_subgraph_to_paths(subgraph, seeds)
        passage_texts = (
            list(passages.retrieve(query, passage_k)) if passages is not None else []
        )
        return synthesize_hybrid(
            query, subgraph, passage_texts, llm, seed_names=seed_names
        )
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
