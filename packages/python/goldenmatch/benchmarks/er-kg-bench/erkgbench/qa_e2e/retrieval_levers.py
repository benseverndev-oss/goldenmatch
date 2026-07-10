"""Lever measurement harness for path-aware retrieval (Lever A).

Reruns the `oracle`/`goldengraph` dials' LOCAL path with a retrieval lever applied between
`_retrieve_local` and `synthesize_local`, returning per-dial answer-match AND the post-lever
bridge-recall (the LLM-FREE guard: a lever that raises answer-match by STRANDING the answer is a
regression, not a win). See docs/superpowers/plans/2026-07-07-goldengraph-path-aware-retrieval.md.

CRITICAL (review finding #2): the stock ablation seeds ONE node, which makes
`filter_subgraph_to_paths`'s anchor-to-anchor bridge inert. This harness seeds MULTI (the same
`k=5` `seed_by_query` shape the product `ask` path uses) via an injectable `seeds_fn`, so the
bench regime matches production.
"""
from __future__ import annotations

from dataclasses import dataclass, field

_DIALS = ("oracle", "goldengraph", "name_only", "none")


@dataclass
class LeverResult:
    lever: str
    #: dial -> mean post-lever bridge-recall (whole_chain); the recall GUARD
    bridge_recall: dict[str, float] = field(default_factory=dict)
    #: dial -> mean answer-match (only when an llm is supplied; else empty)
    answer_match: dict[str, float] = field(default_factory=dict)
    n: int = 0


def _apply(lever: str, subgraph: dict, seeds, *, halo: int,
           question=None, embedder=None, k_hops: int = 4, top_c: int = 3):
    if lever == "none":
        return subgraph
    if lever == "filter_path":
        from goldengraph.subgraph_filter import filter_subgraph_to_paths

        return filter_subgraph_to_paths(subgraph, list(seeds), halo=halo)
    if lever == "candidate":
        from goldengraph.retrieve_paths import prune_to_candidate_paths

        return prune_to_candidate_paths(
            subgraph, list(seeds), question, embedder, k_hops=k_hops, top_c=top_c, halo=halo
        )
    raise ValueError(f"unknown lever {lever!r}")


def measure_lever(
    corpus,
    g,
    typ_of,
    *,
    lever: str,
    seeds_fn,
    llm=None,
    hops: int | None = None,
    node_budget: int | None = None,
    halo: int = 1,
    embedder=None,
    top_c: int = 3,
    k_hops: int = 4,
) -> LeverResult:
    """`seeds_fn(slice_graph, question) -> list[int]` produces the (multi-)seed set. `llm=None`
    runs the recall GUARD only (no synthesis, ~$0 beyond seeding). Wheel-gated (builds a store).
    `lever="candidate"` (Lever C) needs `embedder` (scores candidate end nodes vs the question);
    `top_c`/`k_hops` tune the prune."""
    from goldengraph.answer import _retrieve_local
    from goldengraph.synthesize import synthesize_local

    from . import metrics
    from .ablation import _KEYFN, _build_store
    from .engines.goldengraph import _NODE_BUDGET, _RETRIEVAL_HOPS
    from .gold import gold_chain
    from .scorecard import bridge_recall

    hops = _RETRIEVAL_HOPS if hops is None else hops
    node_budget = _NODE_BUDGET if node_budget is None else node_budget
    chains = {qa.id: gold_chain(g, qa) for qa in corpus.questions}

    out = LeverResult(lever=lever, n=len(corpus.questions))
    for dial in _DIALS:
        km = _KEYFN[dial](corpus, g)
        slice_graph, coverage = _build_store(corpus, g, km, typ_of)
        recalls, ams = [], []
        for qa in corpus.questions:
            seeds = seeds_fn(slice_graph, qa.question)
            ball = _retrieve_local(slice_graph, seeds, max_hops=hops, node_budget=node_budget)
            sub = _apply(lever, ball, seeds, halo=halo, question=qa.question,
                         embedder=embedder, k_hops=k_hops, top_c=top_c)
            recalls.append(bridge_recall(chains[qa.id], sub, coverage)["whole_chain"])
            if llm is not None:
                id2name = {e["entity_id"]: e["canonical_name"] for e in sub.get("entities", ())}
                seed_names = [id2name[s] for s in seeds if s in id2name]
                pred = synthesize_local(qa.question, sub, llm, seed_names=seed_names)
                ams.append(metrics.answer_match(pred, qa.gold_answer))
        out.bridge_recall[dial] = sum(recalls) / len(recalls) if recalls else 0.0
        if ams:
            out.answer_match[dial] = sum(ams) / len(ams)
    return out


def seed_by_query_fn(embedder, *, k: int = 5):
    """A `seeds_fn` that uses the product `seed_by_query` (the k=5 regime `ask` uses)."""
    from goldengraph.embed import seed_by_query

    def _fn(slice_graph, question):
        return seed_by_query(slice_graph, question, embedder, k=k)

    return _fn
