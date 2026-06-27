"""KG/RAG query-routing kernel (slice 1). Heuristic classify_query -> QueryProfile and a
plan_query rule table -> RetrievalPlan. Pure-Python (no wheel). Mirrors the ER auto-config
controller's HeuristicRefitPolicy; an LLM-assisted classifier tier is a slice-3 seam.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

_AGG_RE = re.compile(r"\b(list all|how many|which entities|all entities)\b", re.IGNORECASE)
_TEMPORAL_RE = re.compile(r"\b(as of|at the time|in \d{4}|before \d|after \d)\b", re.IGNORECASE)
_LOOKUP_RE = re.compile(r"^\s*(what is|who is|where is)\b", re.IGNORECASE)


class QueryIntent(StrEnum):
    AGGREGATE = "aggregate"
    TEMPORAL_ASOF = "temporal_asof"
    MULTI_HOP = "multi_hop"
    LOOKUP = "lookup"


@dataclass
class QueryProfile:
    intent: QueryIntent
    anchor_surface: str | None = None
    relation: str | None = None
    as_of: str | None = None
    confidence: float = 0.0


def _detect_intent(query: str) -> QueryIntent:
    # temporal takes precedence over aggregate (a dated set-query is still as-of-flavored)
    if _TEMPORAL_RE.search(query):
        return QueryIntent.TEMPORAL_ASOF
    if _AGG_RE.search(query):
        return QueryIntent.AGGREGATE
    if _LOOKUP_RE.search(query):
        return QueryIntent.LOOKUP
    return QueryIntent.MULTI_HOP


def classify_query(query: str, *, predicates=None) -> QueryProfile:
    """Heuristic intent + (for AGGREGATE) anchor/relation slots. `predicates` is an optional
    set of stored predicate ids (underscored) used to split '<anchor> <relation words>'; when
    absent the relation slot stays None and confidence drops (routes to the safe fallback)."""
    intent = _detect_intent(query)
    return QueryProfile(intent=intent, confidence=0.5 if intent is not QueryIntent.MULTI_HOP else 0.3)
