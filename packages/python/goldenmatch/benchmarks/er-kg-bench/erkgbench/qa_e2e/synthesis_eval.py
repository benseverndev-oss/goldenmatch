"""Stage 0: synthesis-given-gold in isolation -- is SYNTHESIS the bottleneck, or RETRIEVAL?

Hands the model the GOLD subgraph (perfect retrieval) + the question and scores answer-match. Reuses
the scorecard's `synthesis_given_gold` (build the gold chain subgraph -> synthesize_local -> answer_match)
over the engineered corpus, reporting the mean + per-hop curve.

Reading the result (the Stage-0 gate in the distillation design):
  - LOW  given the gold subgraph -> SYNTHESIS is the bottleneck (a weak multi-hop reasoner) -> distill it.
  - HIGH given the gold subgraph but low end-to-end -> RETRIEVAL is the bottleneck (the right subgraph
    isn't reaching synthesis) -> pivot to retrieval, NOT synthesis distillation.
Wheel-free (operates on subgraph dicts, no PyStore).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SynthesisGivenGold:
    mean: float
    by_hop: dict = field(default_factory=dict)
    n: int = 0
    n_failed: int = 0


def evaluate_synthesis_given_gold(*, llm, seed: int = 7, n_questions: int = 80,
                                  ambiguity: float = 0.6, max_hops: int = 4) -> SynthesisGivenGold:
    from .ablation import _typ_of
    from .engineered import generate_engineered
    from .gold import GoldGraph, gold_chain
    from .scorecard_llm import synthesis_given_gold

    corpus = generate_engineered(
        seed=seed, n_questions=n_questions, ambiguity=ambiguity, max_hops=max_hops
    )
    g = GoldGraph.from_corpus(corpus)
    typ_of = _typ_of(g)
    scores: list[float] = []
    by_hop: dict[int, list[float]] = {}
    n_failed = 0
    for qa in corpus.questions:
        chain = gold_chain(g, qa)
        try:
            sc = synthesis_given_gold(qa.question, chain, g, typ_of, qa.gold_answer, llm)
        except Exception:
            sc = 0.0  # fail-soft: a synthesis error is a miss, not a crashed run
            n_failed += 1
        scores.append(sc)
        by_hop.setdefault(qa.hop_count, []).append(sc)
    mean = sum(scores) / len(scores) if scores else 0.0
    return SynthesisGivenGold(
        mean=mean,
        by_hop={h: sum(v) / len(v) for h, v in sorted(by_hop.items())},
        n=len(scores),
        n_failed=n_failed,
    )


def render_md(res: SynthesisGivenGold, *, model: str) -> str:
    hops = " ".join(f"{h}-hop {v:.2f}" for h, v in res.by_hop.items())
    lines = [
        "# Synthesis-given-gold (isolation) -- is synthesis the bottleneck?",
        "",
        f"Engineered corpus, chat model `{model}`. The model is handed the GOLD subgraph (perfect",
        "retrieval) + the question. LOW here => synthesis is the gap (distill it). HIGH here but low",
        "end-to-end => the gap is RETRIEVAL, not synthesis.",
        "",
        f"- synthesis-given-gold answer-match (mean): **{res.mean:.3f}**  ({res.n} questions)",
        f"- by hop: {hops}",
        f"- synthesis failures: {res.n_failed}/{res.n}",
    ]
    return "\n".join(lines) + "\n"
