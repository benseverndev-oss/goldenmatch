"""ER->answer ablation ACROSS the ambiguity sweep.

The per-ambiguity primitive `scorecard_llm.answer_match_ablation` already measures, at a
single ambiguity, each ER dial's answer-match + bridge-recall. This module is the thin
layer the ER->answer experiment adds on top of it: run that primitive over an ambiguity
grid and answer the one question the head-to-head anomaly raised (RESULTS_QA_E2E.md) —
does the ER->answer advantage SURVIVE rising ambiguity (World A: moat real, chase
synthesis) or COLLAPSE (World B: reposition to the cost/multi-hop win)?

Everything here is pure (no wheel, no LLM): it reshapes the primitive's per-ambiguity
output into per-dial curves, computes the ER->answer delta vs the `none` dial, and renders
the World-A/B verdict. The wheel+LLM sweep runner lives alongside (`run_answer_ablation_sweep`)
and feeds this its per-ambiguity dicts.

Spec: docs/superpowers/specs/2026-07-07-goldengraph-er-answer-ablation-design.md
"""
from __future__ import annotations

from dataclasses import dataclass

#: Dial order, worst-ER-last (matches scorecard_llm._DIAL_ORDER).
_DIAL_ORDER = ("oracle", "goldengraph", "name_only", "none")

#: World-A/B decision knob (spec §3). The ER->answer delta at MAX ambiguity must hold at
#: >= this fraction of the delta at MIN ambiguity for "the moat survives ambiguity"
#: (World A). Below it, the delta collapses under noise (World B -> reposition). This is
#: the experiment's FINDING, reported SOFT — never a regression gate.
DELTA_HOLD_FRAC = 0.5


@dataclass
class AnswerAblationSweep:
    #: ascending ambiguity grid actually run
    ambiguities: tuple[float, ...]
    #: dial -> {ambiguity -> mean answer-match}
    answer_match: dict[str, dict[float, float]]
    #: dial -> {ambiguity -> mean bridge-recall}
    bridge_recall: dict[str, dict[float, float]]
    #: dial -> {ambiguity -> answer_match[dial] - answer_match["none"]} (the ER->answer delta)
    delta: dict[str, dict[float, float]]


def aggregate_sweep(per_ambiguity: dict) -> AnswerAblationSweep:
    """Reshape ``{ambiguity: answer_match_ablation-shaped dict}`` into per-dial curves +
    the ER->answer delta (each dial's answer-match minus the `none` dial's, per ambiguity)."""
    ambiguities = tuple(sorted(per_ambiguity))
    answer_match: dict[str, dict[float, float]] = {d: {} for d in _DIAL_ORDER}
    bridge_recall: dict[str, dict[float, float]] = {d: {} for d in _DIAL_ORDER}
    for a in ambiguities:
        res = per_ambiguity[a]
        for d in _DIAL_ORDER:
            answer_match[d][a] = res[d]["answer_match"]["mean"]
            bridge_recall[d][a] = res[d]["bridge_recall"]["mean"]
    delta = {
        d: {a: answer_match[d][a] - answer_match["none"][a] for a in ambiguities}
        for d in _DIAL_ORDER
    }
    return AnswerAblationSweep(
        ambiguities=ambiguities,
        answer_match=answer_match,
        bridge_recall=bridge_recall,
        delta=delta,
    )


def run_answer_ablation_sweep(
    *, seed: int, n_questions: int, ambiguities, max_hops: int, llm
) -> AnswerAblationSweep:
    """Run the existing per-ambiguity primitive over an ambiguity grid and aggregate.

    One SHARED ``llm`` is threaded through every ambiguity so a single cost cap spans the
    whole sweep — the ``llm.exhausted`` short-circuit already lives inside
    ``answer_match_ablation``'s per-question loop, so an exhausted budget cleanly zeros the
    remaining synthesis calls rather than overspending. Needs the ``goldengraph_native``
    wheel (imports are function-local so this MODULE stays wheel-free for the pure layer).
    """
    from .ablation import _typ_of
    from .engineered import generate_engineered
    from .gold import GoldGraph
    from .scorecard_llm import answer_match_ablation

    per_ambiguity: dict[float, dict] = {}
    for a in ambiguities:
        corpus = generate_engineered(
            seed=seed, n_questions=n_questions, ambiguity=a, max_hops=max_hops
        )
        g = GoldGraph.from_corpus(corpus)
        typ_of = _typ_of(g)
        per_ambiguity[a] = answer_match_ablation(corpus, g, typ_of, llm)
    return aggregate_sweep(per_ambiguity)


def _monotonic_in_er(sweep: AnswerAblationSweep, a: float) -> bool:
    """At ambiguity ``a``, answer-match descends oracle >= goldengraph >= name_only >= none."""
    vals = [sweep.answer_match[d][a] for d in _DIAL_ORDER]
    return all(vals[i] + 1e-9 >= vals[i + 1] for i in range(len(vals) - 1))


def _delta_holds(sweep: AnswerAblationSweep) -> bool:
    """World A: a POSITIVE ER->answer delta on clean data that does NOT collapse under
    ambiguity. A non-positive clean-data delta means no moat converts at all -> World B."""
    if not sweep.ambiguities:
        return True
    lo, hi = sweep.ambiguities[0], sweep.ambiguities[-1]
    d_lo, d_hi = sweep.delta["oracle"][lo], sweep.delta["oracle"][hi]
    return d_lo > 1e-9 and d_hi >= DELTA_HOLD_FRAC * d_lo


def sweep_verdict(sweep: AnswerAblationSweep) -> list[tuple[str, bool, bool]]:
    """[(label, passed, is_hard)] mirroring ablation.evaluate_assertions.

    - HARD: answer-match is monotonic in ER quality at EVERY ambiguity (the sanity check —
      if this fails the harness or corpus is broken, not the thesis).
    - SOFT: the ER->answer delta HOLDS under ambiguity (>= DELTA_HOLD_FRAC of its
      clean-data value at max ambiguity). This is the World-A(PASS)/World-B(WARN) FINDING.
    """
    ambs = sweep.ambiguities
    monotonic = all(_monotonic_in_er(sweep, a) for a in ambs)
    lo = f"{ambs[0]:g}" if ambs else "-"
    hi = f"{ambs[-1]:g}" if ambs else "-"
    return [
        (
            "answer-match monotonic in ER quality at every ambiguity "
            "(oracle>=goldengraph>=name_only>=none)",
            monotonic,
            True,
        ),
        (
            f"ER->answer delta holds under ambiguity "
            f"(delta_oracle@{hi} >= {DELTA_HOLD_FRAC:g}*delta_oracle@{lo}) "
            f"— PASS=World A (moat survives), WARN=World B (reposition)",
            _delta_holds(sweep),
            False,
        ),
    ]


def render_sweep_md(sweep: AnswerAblationSweep, *, model: str) -> str:
    """Dial x ambiguity answer-match + bridge-recall tables, the ER->answer delta row, and
    the World-A/B verdict. Regenerating overwrites the file."""
    ambs = sweep.ambiguities
    cols = " | ".join(f"amb={a:g}" for a in ambs)
    sep = "---|" * (len(ambs) + 1)

    def _table(title: str, curve: dict[str, dict[float, float]]) -> list[str]:
        lines = [f"## {title}", "", f"| dial | {cols} |", f"|{sep}"]
        for d in _DIAL_ORDER:
            cells = " | ".join(f"{curve[d][a]:.3f}" for a in ambs)
            lines.append(f"| {d} | {cells} |")
        lines.append("")
        return lines

    lines = [
        "# GoldenGraph ER->answer ablation across the ambiguity sweep",
        "",
        f"Engineered corpus, chat model `{model}`. Extraction held to GOLD (no LLM), so the",
        "ONLY variable across dials is cross-document resolution; retrieval budget is fixed.",
        "The question: does the ER->answer advantage SURVIVE rising ambiguity (World A) or",
        "collapse (World B)? See docs/superpowers/specs/2026-07-07-goldengraph-er-answer-ablation-design.md.",
        "",
    ]
    lines += _table("answer-match by ambiguity", sweep.answer_match)
    lines += _table("bridge-recall by ambiguity (the retrieval-layer advantage)", sweep.bridge_recall)

    # The ER->answer delta row (oracle & goldengraph vs `none`).
    lines += ["## ER->answer delta (dial answer-match minus `none`)", "", f"| dial | {cols} |", f"|{sep}"]
    for d in ("oracle", "goldengraph"):
        cells = " | ".join(f"{sweep.delta[d][a]:+.3f}" for a in ambs)
        lines.append(f"| delta_{d} | {cells} |")
    lines.append("")

    lines += ["## verdict", ""]
    for label, passed, is_hard in sweep_verdict(sweep):
        tag = "PASS" if passed else ("FAIL" if is_hard else "WARN")
        lines.append(f"- [{tag}] {label}{'' if is_hard else ' (soft — the finding)'}")
    return "\n".join(lines) + "\n"
