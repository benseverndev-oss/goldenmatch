"""Stage 0b: does answer-time RETRIEVAL surface the gold chain?

Isolates retrieval on a PERFECT (oracle-resolved) graph -- so any miss is pure retrieval failure, not
extraction/resolution. Builds the oracle store, then per question runs the REAL `seed_by_query` +
`_retrieve_local` and measures two things, the exact failure modes to disambiguate:
  - anchor-seed recall: is the chain's START entity among the embedding seeds? (mis-seeding)
  - gold-chain coverage: what fraction of the chain's entities are in the retrieved ball? (drops chain)

LOW anchor-seed-recall -> the embedder mis-seeds the anchor. LOW chain-coverage with OK seeding -> the
undirected ball doesn't expand along the (directed) relation chain. Needs the wheel (PyStore) + the
embedder (Ollama for local). Entity ids are mapped store-id -> canonical via the _build_store_obj
coverage map.
"""
from __future__ import annotations

from dataclasses import dataclass, field


def _mean(xs) -> float:
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


def _fraction_covered(targets: set, covered: set) -> float:
    """Fraction of `targets` present in `covered` (pure -- the core coverage arithmetic)."""
    return len(targets & covered) / len(targets) if targets else 0.0


@dataclass
class RetrievalCoverage:
    anchor_seed_recall: float
    chain_coverage: float
    by_hop: dict = field(default_factory=dict)  # hop -> mean chain_coverage
    n: int = 0


def evaluate_retrieval_coverage(*, embedder, seed: int = 7, n_questions: int = 40,
                                ambiguity: float = 0.6, max_hops: int = 4,
                                k: int = 5, hops: int = 6, node_budget: int = 256) -> RetrievalCoverage:
    from goldengraph.answer import _retrieve_local
    from goldengraph.embed import seed_by_query

    from . import ablation, dials
    from .engineered import generate_engineered
    from .gold import GoldGraph, gold_chain

    corpus = generate_engineered(
        seed=seed, n_questions=n_questions, ambiguity=ambiguity, max_hops=max_hops
    )
    g = GoldGraph.from_corpus(corpus)
    store, slice_graph, coverage = ablation._build_store_obj(  # noqa: F841 (store kept alive)
        corpus, g, dials.oracle_keys(corpus, g), ablation._typ_of(g)
    )

    def canon(ids) -> set:
        out: set = set()
        for i in ids:
            out |= coverage.get(i, set())
        return out

    seeded, covs = [], []
    by_hop: dict[int, list[float]] = {}
    for qa in corpus.questions:
        chain = gold_chain(g, qa)
        if not chain:
            continue
        chain_canon = {x for (s, _r, o) in chain for x in (s, o)}
        anchor = chain[0][0]
        seeds = seed_by_query(slice_graph, qa.question, embedder, k=k)
        sub = _retrieve_local(slice_graph, seeds, max_hops=hops, node_budget=node_budget)
        seeded.append(1.0 if anchor in canon(seeds) else 0.0)
        cov = _fraction_covered(chain_canon, canon(e["entity_id"] for e in sub.get("entities", ())))
        covs.append(cov)
        by_hop.setdefault(qa.hop_count, []).append(cov)
    return RetrievalCoverage(
        anchor_seed_recall=_mean(seeded),
        chain_coverage=_mean(covs),
        by_hop={h: _mean(v) for h, v in sorted(by_hop.items())},
        n=len(covs),
    )


def render_md(res: RetrievalCoverage, *, embed_model: str) -> str:
    hops = " ".join(f"{h}-hop {v:.2f}" for h, v in res.by_hop.items())
    return "\n".join([
        "# Retrieval coverage (isolation) -- does the answer-time ball surface the gold chain?",
        "",
        f"Oracle-resolved graph (perfect extraction), embedder `{embed_model}`. Per question, run the",
        "REAL seed_by_query + _retrieve_local and check the gold chain. Any miss here is PURE retrieval",
        "(the graph is perfect). LOW anchor-seed = mis-seeding; LOW chain-coverage = the ball drops the chain.",
        "",
        f"- anchor-seed recall (chain start among seeds): **{res.anchor_seed_recall:.3f}**",
        f"- gold-chain entity coverage of the ball (mean): **{res.chain_coverage:.3f}**  ({res.n} questions)",
        f"- chain coverage by hop: {hops}",
    ]) + "\n"
