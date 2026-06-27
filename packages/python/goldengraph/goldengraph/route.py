"""KG/RAG query-routing kernel (slice 1). Heuristic classify_query -> QueryProfile and a
plan_query rule table -> RetrievalPlan. Pure-Python (no wheel). Mirrors the ER auto-config
controller's HeuristicRefitPolicy; an LLM-assisted classifier tier is a slice-3 seam.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

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


_LEADIN_RE = re.compile(
    r"^\s*(?:list all entities that|all entities that|how many entities does|"
    r"which entities)\s+(?P<rest>.+?)\s*[.?]?\s*$",
    re.IGNORECASE,
)


def _split_anchor_relation(rest: str, predicates) -> tuple[str | None, str | None]:
    """Split '<anchor> <relation words>' by matching the LONGEST predicate phrase that is a suffix
    of `rest`; the prefix is the anchor. Without `predicates` the relation can't be split out."""
    rest = rest.strip()
    if not predicates:
        return (rest or None), None
    best = None
    for pred in predicates:
        phrase = pred.replace("_", " ")
        if rest.lower().endswith(phrase.lower()) and (best is None or len(phrase) > len(best[1])):
            best = (pred, phrase)
    if best is None:
        return (rest or None), None
    pred, phrase = best
    anchor = rest[: len(rest) - len(phrase)].strip()
    return (anchor or None), pred


def _extract_agg_slots(query: str, predicates) -> tuple[str | None, str | None]:
    m = _LEADIN_RE.match(query)
    if not m:
        return None, None
    return _split_anchor_relation(m.group("rest"), predicates)


_TEMPORAL_LEADIN_RE = re.compile(
    r"^\s*as of\s+(?P<d>\d+)\s*,\s*what does\s+(?P<rest>.+?)\s*[.?]?\s*$",
    re.IGNORECASE,
)


def _extract_temporal_slots(query: str, predicates):
    """(anchor, relation, as_of) from 'As of <D>, what does <anchor> <relation words>?'."""
    m = _TEMPORAL_LEADIN_RE.match(query)
    if not m:
        return None, None, None
    anchor, relation = _split_anchor_relation(m.group("rest"), predicates)
    return anchor, relation, m.group("d")


def classify_query(query: str, *, predicates=None) -> QueryProfile:
    """Heuristic intent + (for AGGREGATE) anchor/relation slots. `predicates` is an optional
    set of stored predicate ids (underscored) used to split '<anchor> <relation words>'; when
    absent the relation slot stays None and confidence drops (routes to the safe fallback)."""
    intent = _detect_intent(query)
    if intent is QueryIntent.AGGREGATE:
        anchor, relation = _extract_agg_slots(query, predicates)
        conf = 0.9 if (anchor and relation) else 0.5
        return QueryProfile(intent=intent, anchor_surface=anchor, relation=relation, confidence=conf)
    if intent is QueryIntent.TEMPORAL_ASOF:
        anchor, relation, as_of = _extract_temporal_slots(query, predicates)
        conf = 0.9 if (anchor and relation and as_of) else 0.5
        return QueryProfile(intent=intent, anchor_surface=anchor, relation=relation,
                            as_of=as_of, confidence=conf)
    conf = 0.5 if intent is not QueryIntent.MULTI_HOP else 0.3
    return QueryProfile(intent=intent, confidence=conf)


MIN_CONF = 0.8  # below this, a specialized intent routes to the safe general mode


@dataclass
class RetrievalPlan:
    mode: str
    note: str | None = None
    params: dict = field(default_factory=dict)


def plan_query(profile: QueryProfile) -> RetrievalPlan:
    if (
        profile.intent is QueryIntent.AGGREGATE
        and profile.confidence >= MIN_CONF
        and profile.anchor_surface
        and profile.relation
    ):
        return RetrievalPlan(mode="aggregate")
    if profile.intent is QueryIntent.TEMPORAL_ASOF:
        if (
            profile.confidence >= MIN_CONF
            and profile.anchor_surface
            and profile.relation
            and profile.as_of
        ):
            return RetrievalPlan(mode="as_of")
        return RetrievalPlan(mode="local")  # low-confidence temporal -> safe general mode
    if profile.intent is QueryIntent.MULTI_HOP:
        return RetrievalPlan(mode="hybrid")
    return RetrievalPlan(mode="local")  # LOOKUP + low-confidence fallbacks


class QueryClassifier(Protocol):
    def classify(self, query: str, *, predicates=None) -> QueryProfile: ...


def resolve_profile(query: str, *, predicates=None,
                    llm_classifier: QueryClassifier | None = None) -> QueryProfile:
    """Two-tier: heuristic FIRST; escalate to the injected classifier ONLY when the heuristic is
    below MIN_CONF AND a classifier is given; the classifier's result wins only if strictly more
    confident (so a confidently-abstaining tier-2 keeps the heuristic -> safe local route)."""
    h = classify_query(query, predicates=predicates)
    if h.confidence >= MIN_CONF or llm_classifier is None:
        return h
    ll = llm_classifier.classify(query, predicates=predicates)
    return ll if ll.confidence > h.confidence else h


def _strip_fence(raw: str) -> str:
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()


class LLMQueryClassifier:
    """Tier-2 classifier: prompt an LLMClient for {intent, anchor, relation, as_of}; defensive
    parse -> QueryProfile. Budget-capped (max_calls). Fail-open: any failure (budget, exception,
    bad JSON, out-of-vocab relation) -> abstain QueryProfile(MULTI_HOP, confidence=0.0)."""

    _PROMPT = (
        "Classify this knowledge-graph question. Reply with ONLY a JSON object:\n"
        '{{"intent": "aggregate|temporal_asof|lookup|multi_hop", "anchor": "<entity or null>", '
        '"relation": "<one of: {preds}> or null", "as_of": "<integer date or null>"}}\n'
        "Question: {q}"
    )

    def __init__(self, llm, *, max_calls: int = 5):
        self._llm = llm
        self._max_calls = max_calls
        self._calls = 0

    def classify(self, query: str, *, predicates=None) -> QueryProfile:
        abstain = QueryProfile(QueryIntent.MULTI_HOP, confidence=0.0)
        if self._calls >= self._max_calls:
            return abstain
        self._calls += 1
        try:
            preds = ", ".join(sorted(predicates)) if predicates else ""
            raw = self._llm.complete(self._PROMPT.format(preds=preds, q=query))
            data = json.loads(_strip_fence(raw))
        except Exception:
            return abstain
        try:
            intent = QueryIntent(str(data.get("intent", "")).strip().lower())
        except ValueError:
            return abstain
        anchor = data.get("anchor") or None
        relation = data.get("relation") or None
        if relation is not None and (not predicates or relation not in predicates):
            return abstain  # hallucinated / out-of-vocab relation
        as_of = str(data["as_of"]) if data.get("as_of") not in (None, "") else None
        return QueryProfile(intent=intent, anchor_surface=anchor, relation=relation,
                            as_of=as_of, confidence=0.85)
