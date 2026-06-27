"""Slice 4a gate. Build a CAPABILITY workload (B1 aggregation + B2 temporal question texts) and a
LOOKUP workload ("what is <concept>?"); assert the planner routes capability->FUZZY, lookup->EXACT
(wheel-free), and reuse slice-D kg_scorecard (+ its MOAT_MARGIN) to prove FUZZY (goldengraph dial)
beats EXACT (exact_match dial) on the workload's capability -- the unification thesis, MEASURED
(needs the wheel).
"""
from __future__ import annotations

from dataclasses import dataclass

from goldengraph.unified import ResolutionTier, plan_resolution, profile_workload

from .engineered import RELATION_SCHEMA, _load_entities


def _capability_queries(seed: int, n_anchors: int, n_facts: int):
    from .aggregation import generate_aggregation
    from .temporal import generate_temporal

    _d, agg_qs = generate_aggregation(seed=seed, n_anchors=n_anchors, ambiguity=0.0)
    _d2, _f, tmp_qs = generate_temporal(seed=seed, n_facts=n_facts, ambiguity=0.6)
    return [q.question for q in agg_qs if q.kind == "list"] + [q.question for q in tmp_qs]


def _lookup_queries():
    return [f"what is {e.canonical}?" for e in _load_entities()]


def routing_correctness(*, seed: int, n_anchors: int, n_facts: int) -> dict:
    preds = set(RELATION_SCHEMA)
    cap = plan_resolution(profile_workload(_capability_queries(seed, n_anchors, n_facts), predicates=preds))
    look = plan_resolution(profile_workload(_lookup_queries(), predicates=preds))
    return {"capability_tier": cap.resolution_tier.value, "lookup_tier": look.resolution_tier.value,
            "capability_fraction": cap.capability_fraction, "lookup_fraction": look.capability_fraction}


@dataclass
class UnifiedResult:
    capability_tier: str
    lookup_tier: str
    capability_fraction: float
    lookup_fraction: float
    agg_delta: float     # goldengraph(FUZZY) - exact_match(EXACT) on aggregation set-F1 (kg_scorecard)
    bridge_delta: float  # ... on bridge-recall


def evaluate_assertions(res: UnifiedResult):
    from .kg_scorecard import MOAT_MARGIN  # reuse the slice-D margin (no drift)

    return [
        (f"capability workload -> FUZZY, lookup -> EXACT (cap={res.capability_tier}, look={res.lookup_tier})",
         res.capability_tier == ResolutionTier.FUZZY.value and res.lookup_tier == ResolutionTier.EXACT.value, True),
        (f"capability_fraction {res.capability_fraction:.3f} >= 0.5 > lookup_fraction {res.lookup_fraction:.3f}",
         res.capability_fraction >= 0.5 and res.lookup_fraction < 0.5, True),
        (f"chosen FUZZY tier WINS capability: agg_delta {res.agg_delta:.3f} & bridge_delta {res.bridge_delta:.3f} >= {MOAT_MARGIN} (measured, slice D)",
         res.agg_delta >= MOAT_MARGIN and res.bridge_delta >= MOAT_MARGIN, True),
    ]


def gate_exit_code(res: UnifiedResult) -> int:
    return 1 if any(h and not ok for _l, ok, h in evaluate_assertions(res)) else 0


def run_justification(*, seed: int, n_questions: int, n_anchors: int) -> tuple[float, float]:
    """kg_scorecard deltas: goldengraph(FUZZY) - exact_match(EXACT) on agg + bridge. Needs the wheel."""
    from .kg_scorecard import run_scorecard_deterministic

    sc = run_scorecard_deterministic(seed=seed, n_questions=n_questions, n_anchors=n_anchors, ambiguity=0.6)
    return (sc.aggregation_f1["goldengraph"] - sc.aggregation_f1["exact_match"],
            sc.bridge_recall["goldengraph"] - sc.bridge_recall["exact_match"])


def run_unified_deterministic(*, seed: int, n_anchors: int, n_facts: int, n_questions: int) -> UnifiedResult:
    rc = routing_correctness(seed=seed, n_anchors=n_anchors, n_facts=n_facts)
    agg_d, bridge_d = run_justification(seed=seed, n_questions=n_questions, n_anchors=n_anchors)
    return UnifiedResult(capability_tier=rc["capability_tier"], lookup_tier=rc["lookup_tier"],
                         capability_fraction=rc["capability_fraction"], lookup_fraction=rc["lookup_fraction"],
                         agg_delta=agg_d, bridge_delta=bridge_d)


def render_unified_md(res: UnifiedResult) -> str:
    lines = [
        "# GoldenGraph unified planner gate (slice 4a, no LLM)",
        "",
        "The meta-kernel JOIN: a query workload's capability demand picks the ER resolution tier, and",
        "slice-D's measured dial scorecard proves the chosen tier WINS the workload's capability.",
        "(4a is the decision + its justification; nothing consumes the plan yet -- 4b wires it.)",
        "",
        f"- capability workload: tier={res.capability_tier}  (capability_fraction {res.capability_fraction:.3f})",
        f"- lookup workload:     tier={res.lookup_tier}  (capability_fraction {res.lookup_fraction:.3f})",
        f"- FUZZY-vs-EXACT measured deltas (slice D): aggregation {res.agg_delta:.3f}, bridge-recall {res.bridge_delta:.3f}",
        "",
        "## verdicts",
        "",
    ]
    for label, ok, _h in evaluate_assertions(res):
        lines.append(f"- [{'PASS' if ok else 'FAIL'}] {label}")
    return "\n".join(lines) + "\n"
