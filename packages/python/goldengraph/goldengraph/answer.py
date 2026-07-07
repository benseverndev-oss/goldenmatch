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


def _add_refs(refs_out, edges) -> None:
    """Collect the `source_refs` (owning-document ids) of `edges` into `refs_out` (a set), when a
    collector is supplied. The provenance behind a retrieval/traversal step -- intersected with the
    gold supporting-fact ids to make supporting-fact recall measurable. No-op when `refs_out` is None."""
    if refs_out is None:
        return
    for e in edges:
        refs_out.update(e.get("source_refs", ()))


def aggregate_members(slice_graph, anchor_surface: str, relation: str, *, refs_out=None) -> set[str]:
    """Engine-native exact aggregation: seed the anchor by name, 1-hop ball, return the canonical
    NAMES of objects on edges (subj in seeds, predicate==relation). LLM-FREE."""
    seeds = slice_graph.seeds_by_name(anchor_surface)
    if not seeds:
        return set()
    sub = slice_graph.query(seeds, 1)
    id_to_name = {e["entity_id"]: e["canonical_name"] for e in sub.get("entities", ())}
    seedset = set(seeds)
    matched = [
        e for e in sub.get("edges", ())
        if e["subj"] in seedset and e["predicate"] == relation and e["obj"] in id_to_name
    ]
    _add_refs(refs_out, matched)
    return {id_to_name[e["obj"]] for e in matched}


def asof_object(slice_graph, anchor_surface: str, relation: str, *, refs_out=None) -> str | None:
    """The object on a (subj==seed, predicate==relation) edge present IN THIS SLICE (the slice
    already encodes the as-of window). The aggregate traversal returning ONE object. LLM-free."""
    seeds = slice_graph.seeds_by_name(anchor_surface)
    if not seeds:
        return None
    sub = slice_graph.query(seeds, 1)
    id_to_name = {e["entity_id"]: e["canonical_name"] for e in sub.get("entities", ())}
    seedset = set(seeds)
    matched = [
        e for e in sub.get("edges", ())
        if e["subj"] in seedset and e["predicate"] == relation and e["obj"] in id_to_name
    ]
    _add_refs(refs_out, matched)
    objs = {id_to_name[e["obj"]] for e in matched}
    objs.discard(anchor_surface)
    return next(iter(sorted(objs)), None)


def _norm_rel(s: str) -> str:
    return " ".join(str(s).lower().replace("_", " ").split())


def _rel_match(edge_pred: str, query_rel: str) -> bool:
    """Lenient predicate match: normalize (lowercase, underscore<->space) then equality or substring
    either way -- so the model's 'was acquired by' / 'works_at' matches the question's 'acquired' /
    'works at'. Tolerates the ~0.85-accurate extracted predicates."""
    a, b = _norm_rel(edge_pred), _norm_rel(query_rel)
    return bool(a) and bool(b) and (a == b or b in a or a in b)


def _bridge_surfaces(slice_graph, ids, id_to_name) -> set:
    """Under-merge bridge: expand each id to ALL entity_ids sharing its canonical name. The store
    under-merges -- the same surface form gets several entity_ids, so an edge's object (an *in*-copy,
    a pure sink) is a different id than the *out*-copy that owns the chain's next edge. Re-seeding by
    canonical name unions the unmerged siblings so the walk crosses the bridge entity. (Measured: 27
    of 29 multi-hop walk deaths landed on a sink-copy with zero outgoing edges -- this is that fix.)"""
    out = set()
    for i in ids:
        out.add(i)
        name = id_to_name.get(i)
        if name:
            out.update(slice_graph.seeds_by_name(name))
    return out


def trace_chain(slice_graph, anchor_surface: str, relation_chain, *, refs_out=None) -> str | None:
    """Relation-guided multi-hop walk: seed the anchor by name, then for each named relation follow
    the matching outgoing edge to the next node(s). Returns the final node's canonical name. LLM-FREE
    -- the directed walk IS the answer (the graph has at most one edge per (entity, relation)), so it
    hands synthesis nothing to drown in. The fix for multi-hop synthesis-over-the-ball failure.

    Each hop bridges the reached nodes across the store's entity under-merge (``_bridge_surfaces``):
    without it the walk strands on the object's sink-copy, whose unmerged sibling owns the next edge.
    """
    import os

    dbg = os.environ.get("GOLDENGRAPH_CHAIN_DEBUG", "") not in ("", "0", "false")
    seeds = slice_graph.seeds_by_name(anchor_surface)
    if not seeds:
        if dbg:
            print(f"[chain] anchor {anchor_surface!r} NOT SEEDED", flush=True)
        return None
    frontier = set(seeds)
    id_to_name: dict = {}
    for hop, rel in enumerate(relation_chain, 1):
        sub = slice_graph.query(list(frontier), 1)
        id_to_name = {e["entity_id"]: e["canonical_name"] for e in sub.get("entities", ())}
        out_edges = [e for e in sub.get("edges", ()) if e["subj"] in frontier and e["obj"] in id_to_name]
        matched_fwd = [e for e in out_edges if _rel_match(e["predicate"], rel)]
        nxt = {e["obj"] for e in matched_fwd}
        _add_refs(refs_out, matched_fwd)  # provenance of the edge(s) this hop traversed
        reversed_used = False
        if not nxt:
            # direction-tolerant fallback: the 7B extracts passive/locative phrasings ("X was authored
            # by Y", "X is located in Y") with the edge pointing object->subject, opposite the forward
            # walk. When no FORWARD edge matches, accept a REVERSED edge of the same relation and take
            # its subject as the next node. Scoped (only fires when forward yields nothing) and safe in
            # the engineered corpus (at most one edge per (entity, relation), so the reversed edge is
            # the same semantic link extracted backwards). Measured: 6 of 8 hop-1 walk deaths.
            in_edges = [e for e in sub.get("edges", ())
                        if e["obj"] in frontier and e["subj"] in id_to_name]
            matched_rev = [e for e in in_edges if _rel_match(e["predicate"], rel)]
            nxt = {e["subj"] for e in matched_rev}
            _add_refs(refs_out, matched_rev)  # provenance of the reversed edge(s) traversed
            reversed_used = bool(nxt)
        if dbg:
            avail = sorted({_norm_rel(e["predicate"]) for e in out_edges})
            tag = " REVERSED-FALLBACK" if reversed_used else ""
            print(f"[chain] hop{hop} rel={rel!r} frontier={len(frontier)} matched={len(nxt)}{tag} "
                  f"avail={avail[:8]}", flush=True)
        if not nxt:
            if dbg:
                print(f"[chain] DIED at hop{hop}: no {rel!r} edge (either direction) from frontier",
                      flush=True)
            return None
        # bridge the reached nodes across the under-merge so the NEXT hop sees the sibling's out-edges
        frontier = _bridge_surfaces(slice_graph, nxt, id_to_name)
    names = sorted({id_to_name[i] for i in frontier if i in id_to_name} - {anchor_surface})
    if dbg:
        print(f"[chain] OK -> {names[:3]}", flush=True)
    return names[0] if names else None


def _format_aggregate(members: set[str]) -> str:
    return ", ".join(sorted(members)) if members else "(none found)"


def _slice_predicates(slice_graph) -> set[str]:
    """Distinct edge predicates in the slice -- the relation vocabulary for slot extraction."""
    ids = [e["entity_id"] for e in slice_graph.entities()]
    if not ids:
        return set()
    return {e["predicate"] for e in slice_graph.query(ids, 1).get("edges", ())}


def _canon_query_rel(rel, schema):
    """Map a query relation to the discovered schema's canonical label, so it matches the canonicalized
    edge predicates -- the QUERY side of schema discovery. When the schema relabels a synonym cluster
    (e.g. {'located in','sits within'} -> 'sits_within'), the edges become 'sits_within' but the query
    still says 'located in'; routing the query relation through the SAME schema realigns them. Surface
    unchanged when there is no schema or no match."""
    if not rel or schema is None:
        return rel
    m = schema.match(rel)
    return m[0] if m is not None else rel


def _hybrid_filter_mode() -> str:
    """Hybrid subgraph filter selector, read at call time. "" / "none" / unset =
    off (pass the full ball; the measured 0.420 control). "path" = path-preserving
    prune (`subgraph_filter.filter_subgraph_to_paths`)."""
    import os

    return os.environ.get("GOLDENGRAPH_HYBRID_FILTER", "").strip().lower()


def _local_filter_mode() -> str:
    """`GOLDENGRAPH_LOCAL_FILTER` selector, read at call time. "" / "none" / unset = off
    (pass the full ball -- byte-identical to the pre-2026-07-07 local path). "path" =
    anchor-to-anchor path prune (`subgraph_filter.filter_subgraph_to_paths`, Lever A -- REFUTED,
    see results/RESULTS_PATH_AWARE_RETRIEVAL.md). "candidate" = answer-candidate-scored prune
    (`retrieve_paths.prune_to_candidate_paths`, Lever C). Both localize the ER-answer ablation's
    multi-hop miss to PATH-SELECTION in the local ball -- the answer is present but buried among
    distractor edges. Both are predicate-blind (dodge the 2026-06-22 predicate-focus revert)."""
    import os

    return os.environ.get("GOLDENGRAPH_LOCAL_FILTER", "").strip().lower()


def _local_filter_halo() -> int:
    """`GOLDENGRAPH_LOCAL_FILTER_HALO` (default 1). Non-int -> 1."""
    import os

    try:
        return int(os.environ.get("GOLDENGRAPH_LOCAL_FILTER_HALO", "1"))
    except ValueError:
        return 1


def _local_filter_topc() -> int:
    """`GOLDENGRAPH_LOCAL_FILTER_TOPC` (default 3) -- Lever C's #candidate paths. Non-int -> 3."""
    import os

    try:
        return int(os.environ.get("GOLDENGRAPH_LOCAL_FILTER_TOPC", "3"))
    except ValueError:
        return 3


def _local_filter_khops() -> int:
    """`GOLDENGRAPH_LOCAL_FILTER_KHOPS` (default 4) -- Lever C's candidate reach. Non-int -> 4."""
    import os

    try:
        return int(os.environ.get("GOLDENGRAPH_LOCAL_FILTER_KHOPS", "4"))
    except ValueError:
        return 4


def _apply_local_filter(subgraph: dict, seeds, *, question=None, embedder=None) -> dict:
    """Gated path-selection prune of the local retrieval ball.

    Off (default) -> `subgraph` unchanged (byte-identical local path). `GOLDENGRAPH_LOCAL_FILTER`:
    - `path` -> Lever A: seeds + anchor-to-anchor shortest paths + `halo` (REFUTED -- recall-safe
      only where the answer sits on an anchor-to-anchor path or within halo of a seed; a single
      seed makes the bridge inert; measured to strand the chain on the multi-seed regime).
    - `candidate` -> Lever C: seeds + halo + seed->top-`c` query-relevant candidate paths
      (`prune_to_candidate_paths`). Needs `question` + `embedder`; if either is None it no-ops
      safely (a caller without an embedder degrades to the full ball, never crashes).
    Worth is decided by the bench bridge-recall guard on the MULTI-seed regime, not asserted here."""
    mode = _local_filter_mode()
    if mode == "path":
        from .subgraph_filter import filter_subgraph_to_paths

        return filter_subgraph_to_paths(subgraph, list(seeds), halo=_local_filter_halo())
    if mode == "candidate":
        if question is None or embedder is None:
            return subgraph
        from .retrieve_paths import prune_to_candidate_paths

        return prune_to_candidate_paths(
            subgraph,
            list(seeds),
            question,
            embedder,
            k_hops=_local_filter_khops(),
            top_c=_local_filter_topc(),
            halo=_local_filter_halo(),
        )
    return subgraph


def _bridge_enabled() -> bool:
    """`GOLDENGRAPH_RETRIEVAL_BRIDGE` gate (default off). On -> the local/hybrid retrieval ball is
    built with `_retrieve_local_bridged` (same-name under-merge bridging) instead of `_retrieve_local`."""
    import os

    return os.environ.get("GOLDENGRAPH_RETRIEVAL_BRIDGE", "0") not in ("0", "false", "")


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


def _retrieve_local_bridged(slice_graph, seeds, *, max_hops: int, node_budget: int) -> dict:
    """Like `_retrieve_local`, but at each hop bridges the reached frontier across same-NAME
    under-merged siblings (the proven `trace_chain` mechanism), so an answer stranded behind a split
    bridge-entity (a sink-copy with no out-edge whose source-copy owns the next hop) enters the ball.
    The ball is a connectivity-SUPERSET (not pruned to the seed-connected component); `node_budget` is
    the only bound on its growth. Opt-in via the `GOLDENGRAPH_RETRIEVAL_BRIDGE` gate."""
    if not seeds:
        return slice_graph.query(seeds, max_hops)
    frontier = set(seeds)
    ents: dict = {}            # dedup by entity_id
    edges: list = []
    seen: set = set()          # dedup edges by (subj, predicate, obj)
    for _hop in range(max(max_hops, 1)):
        sub = slice_graph.query(list(frontier), 1)
        id_to_name = {e["entity_id"]: e["canonical_name"] for e in sub.get("entities", ())}
        for e in sub.get("entities", ()):
            ents.setdefault(e["entity_id"], e)
        for ed in sub.get("edges", ()):
            k = (ed["subj"], ed["predicate"], ed["obj"])
            if k not in seen:
                seen.add(k)
                edges.append(ed)
        if len(ents) >= node_budget:
            break
        # next frontier: the reached ids, BRIDGED across same-name siblings
        frontier = _bridge_surfaces(slice_graph, set(id_to_name), id_to_name)
    return {"entities": list(ents.values()), "edges": edges}


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
    query_schema: object | None = None,
    provenance_out: set | None = None,
    entity_index: object | None = None,
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
        # Query-side schema canonicalization: route the query's relation(s) through the discovered
        # schema so they match the (relabeled) canonical edge predicates. Without this, a cluster
        # relabeled to a non-canonical synonym strands every query that used the canonical word.
        if query_schema is not None:
            if profile.relation:
                profile.relation = _canon_query_rel(profile.relation, query_schema)
            if profile.relation_chain:
                profile.relation_chain = tuple(
                    _canon_query_rel(r, query_schema) for r in profile.relation_chain
                )
        plan = plan_query(profile)
        if plan.mode == "aggregate" and profile.anchor_surface and profile.relation:
            return _format_aggregate(
                aggregate_members(slice_graph, profile.anchor_surface, profile.relation,
                                  refs_out=provenance_out)
            )
        if plan.mode == "as_of" and profile.anchor_surface and profile.relation and profile.as_of:
            # the date IS the slice time -> override the caller's valid_t for this temporal query
            try:
                d = int(profile.as_of)
            except ValueError:
                d = None
            if d is not None:
                obj = asof_object(store.as_of(d, tx_t), profile.anchor_surface, profile.relation,
                                  refs_out=provenance_out)
                return obj if obj is not None else "(unknown)"
        if plan.mode == "chain" and profile.anchor_surface and profile.relation_chain:
            ans = trace_chain(slice_graph, profile.anchor_surface, profile.relation_chain,
                              refs_out=provenance_out)
            if ans is not None:
                return ans
            # walk hit a missing/mislabeled edge -> fall through to the general retrieval+synthesis
        # clamp: a specialized plan that did NOT return must not carry an invalid mode into the
        # `if mode not in ("local","hybrid"): raise` guard below
        mode = plan.mode if plan.mode in ("local", "hybrid", "global") else "local"
    if mode == "global":
        communities = slice_graph.communities()[:max_communities]
        views = [slice_graph.query(c["members"], 1) for c in communities]
        for v in views:
            _add_refs(provenance_out, v.get("edges", ()))
        return synthesize_global(query, views, llm)
    if mode not in ("local", "hybrid"):
        raise ValueError(f"mode must be 'local', 'hybrid', or 'global', got {mode!r}")
    seeds = seed_by_query(slice_graph, query, embedder, k=k, index=entity_index)
    _retrieve = _retrieve_local_bridged if _bridge_enabled() else _retrieve_local
    subgraph = _retrieve(slice_graph, seeds, max_hops=hops, node_budget=node_budget)
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
        _add_refs(provenance_out, subgraph.get("edges", ()))  # provenance of the synthesized ball
        return synthesize_hybrid(
            query, subgraph, passage_texts, llm, seed_names=seed_names
        )
    # Gated path-preserving prune of the ball (default off = byte-identical). The
    # ER-answer ablation localized the multi-hop miss to path-selection in this ball.
    subgraph = _apply_local_filter(subgraph, seeds, question=query, embedder=embedder)
    id_to_name = {
        e["entity_id"]: e["canonical_name"] for e in subgraph.get("entities", ())
    }
    seed_names = [id_to_name[s] for s in seeds if s in id_to_name]
    _add_refs(provenance_out, subgraph.get("edges", ()))  # provenance of the (filtered) ball
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
