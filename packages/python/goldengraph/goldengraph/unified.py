"""Slice 4a: workload-aware resolution planner -- the meta-kernel join. Profile a query workload via
the slice-3 router and pick the ER resolution tier the workload demands (capability-heavy -> high-
recall FUZZY; lookup -> cheap EXACT). Pure-Python (reuses route). Nothing CONSUMES the plan yet (4b
wires it into ingest); 4a is the grounded decision + (gate side) its measured justification.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .route import QueryIntent, plan_query, resolve_profile

#: ER-demanding intents -- weak ER costs these (A multi-hop reachability, B1 aggregation, B2 temporal).
_CAPABILITY_INTENTS = frozenset({QueryIntent.AGGREGATE, QueryIntent.TEMPORAL_ASOF, QueryIntent.MULTI_HOP})

CAP_THRESHOLD = 0.5  # >= this fraction of capability-demanding queries -> high-recall ER


class ResolutionTier(StrEnum):
    EXACT = "exact"                  # name-only merge (cheap)
    FUZZY = "fuzzy"                  # name+type (current default)
    FUZZY_CONTEXT = "fuzzy_context"  # name+type+context (+13pp lever; a 4b refinement)


@dataclass
class WorkloadProfile:
    intents: dict
    capability_fraction: float
    retrieval_modes_needed: set


@dataclass
class UnifiedPlan:
    resolution_tier: ResolutionTier
    retrieval_modes_needed: set
    capability_fraction: float
    rationale: str


def profile_workload(queries, *, predicates=None, llm_classifier=None) -> WorkloadProfile:
    intents: dict = {}
    modes: set = set()
    cap = 0
    for q in queries:
        p = resolve_profile(q, predicates=predicates, llm_classifier=llm_classifier)
        intents[p.intent] = intents.get(p.intent, 0) + 1
        modes.add(plan_query(p).mode)
        if p.intent in _CAPABILITY_INTENTS:
            cap += 1
    n = len(queries) or 1
    return WorkloadProfile(intents=intents, capability_fraction=cap / n, retrieval_modes_needed=modes)


def plan_resolution(wp: WorkloadProfile) -> UnifiedPlan:
    if wp.capability_fraction >= CAP_THRESHOLD:
        tier = ResolutionTier.FUZZY
        why = (f"{wp.capability_fraction:.0%} capability-demanding queries (aggregation/temporal/"
               "multi-hop) -> high-recall FUZZY ER (A-D: exact-match ER == no-merge on these; "
               "FUZZY_CONTEXT is the 4b upgrade for reachability-heavy workloads)")
    else:
        tier = ResolutionTier.EXACT
        why = f"only {wp.capability_fraction:.0%} capability-demanding -> cheap EXACT ER suffices"
    return UnifiedPlan(resolution_tier=tier, retrieval_modes_needed=wp.retrieval_modes_needed,
                       capability_fraction=wp.capability_fraction, rationale=why)
