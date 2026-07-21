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
    relation_chain: tuple[str, ...] | None = None  # multi-hop: the named relations to follow in order
    # True when the relation_chain order is authoritative (the engineered template
    # states it explicitly). False when it's a proximity HINT from free-NL extraction
    # -- the caller then validates order against the graph (tries permutations).
    chain_ordered: bool = True


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


#: "Starting from <anchor>, follow the relation <r1>, then <r2>, ... . What entity ..." -- the
#: engineered multi-hop form. The named relations make a DETERMINISTIC walk (at most one edge per
#: (entity, relation)), so it's answerable LLM-free by tracing the chain -- the fix for synthesis
#: drowning in the retrieved ball (measured: gold-chain synthesis 1.00, ball synthesis 0.15).
_CHAIN_RE = re.compile(
    r"^\s*starting from\s+(?P<anchor>.+?),\s*follow the relation\s+(?P<chain>.+?)\.\s*what entity",
    re.IGNORECASE,
)


def _extract_chain_slots(query: str):
    """(anchor, relation_chain) from the 'Starting from X, follow the relation R1, then R2.' form."""
    m = _CHAIN_RE.match(query)
    if not m:
        return None, None
    anchor = m.group("anchor").strip()
    chain = tuple(s.strip() for s in m.group("chain").split(", then ") if s.strip())
    return (anchor or None), (chain or None)


def _extract_temporal_slots(query: str, predicates):
    """(anchor, relation, as_of) from 'As of <D>, what does <anchor> <relation words>?'."""
    m = _TEMPORAL_LEADIN_RE.match(query)
    if not m:
        return None, None, None
    anchor, relation = _split_anchor_relation(m.group("rest"), predicates)
    return anchor, relation, m.group("d")


# --- template-free NL multi-hop chain extraction -----------------------------
# Real multi-hop questions ("Who is the spouse of the director of Inception?")
# never match the engineered `_CHAIN_RE` template, so they lost the deterministic
# LLM-free `trace_chain` walk (measured: gold-chain synthesis 1.00 vs ball
# synthesis ~0.15). This recovers (anchor, relation_chain) from natural language
# WITHOUT an LLM, grounded in the slice's actual vocabularies:
#   - anchor = the longest stored ENTITY NAME occurring in the question;
#   - relations = the PREDICATE ids whose salient token appears in the question,
#     ordered by surface proximity to the anchor (the first hop's relation is
#     syntactically adjacent to the anchor in of-/possessive nesting, for both
#     "R2 of R1 of ANCHOR" and "ANCHOR's R1's R2").
# It is deliberately conservative and layered so a mis-parse degrades to today's
# retrieval+synthesis path instead of a confident wrong answer:
#   1. it only produces a chain when a REAL anchor AND >=1 real predicate ground;
#   2. the completeness guard (below) abstains when an unmapped content word sits
#      before an "of"/"by" marker -- the truncated-chain case that WOULD complete
#      early at a wrong intermediate node;
#   3. `_trace_chain_any_order` returns only when the graph confirms the hinted
#      order, or (hint failed) a unique fallback order completes; otherwise None.
# The residual it does NOT catch: a mis-parse whose (grounded) relations happen to
# form a DIFFERENT chain that still completes to a valid terminal -- rare given the
# grounding + guard, and no worse than the LLM synthesis path it replaces. So the
# guarantee is "a mis-parse that fails to walk falls through", not "every wrong
# reading is impossible"; `ask()` only falls back when the walk returns None.

_REL_STOPWORDS = frozenset({
    # function words / determiners / prepositions / conjunctions
    "the", "a", "an", "of", "is", "are", "was", "were", "be", "been", "being",
    "by", "to", "in", "at", "on", "for", "and", "or", "with", "from", "into",
    "as", "then", "so", "but", "if", "than", "about",
    # WH / interrogatives
    "who", "whom", "whose", "what", "which", "where", "when", "why", "how",
    # pronouns
    "he", "she", "it", "its", "they", "them", "their", "his", "her", "him",
    "this", "that", "these", "those", "we", "you", "i",
    # auxiliaries / light verbs
    "does", "did", "do", "done", "has", "have", "had", "reach", "give", "gives",
    "given", "name", "named", "call", "called", "get", "gets", "tell",
    # filler / template nouns (not relations)
    "entity", "canonical", "person", "people", "thing", "things", "one",
    "someone", "something", "there", "here",
})


def _predicate_salient_tokens(predicate: str) -> list[str]:
    """Content tokens of a predicate id (>=4 chars, non-stopword). The graph's
    predicates are extracted VERB phrases (`directed_by`, `married_to`,
    `located_in`), so a question's verb form ("who directed", "is married to",
    "located in") matches on the shared content token even when the full phrase
    doesn't appear verbatim."""
    toks = _norm(predicate).split()
    return [t for t in toks if len(t) >= 4 and t not in _REL_STOPWORDS]


def _norm(s: str) -> str:
    # lowercase; every non-alphanumeric run (underscores, commas, '?', "'s") -> a
    # single space, so tokens are punctuation-free ("whom," -> "whom", "X's" -> "x s").
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(s).lower()).split())


def _common_prefix_len(a: str, b: str) -> int:
    n = 0
    for ca, cb in zip(a, b):
        if ca != cb:
            break
        n += 1
    return n


def _stem_match(a: str, b: str) -> bool:
    """True if two tokens are the same word or share a >=5-char stem -- a
    lightweight, LLM-free bridge for morphological variants that lets a question's
    noun form reach the graph's verb predicate ('director'/'directed',
    'location'/'located', 'acquirer'/'acquired', 'author'/'authored'). Requires
    both tokens >=5 chars so short words still need exact equality (no spurious
    'part'/'party' bridges). Pure synonyms with no shared stem (spouse/married)
    are out of reach here -- that needs the embedding or LLM classifier tier."""
    if a == b:
        return True
    if len(a) < 5 or len(b) < 5:
        return False
    return _common_prefix_len(a, b) >= 5


def _predicate_token_index(predicates) -> list[tuple[str, list[str]]]:
    """Precompute ``[(predicate, salient_tokens), ...]`` ONCE per query: predicates
    SORTED (so a score tie resolves to the lexicographically-smallest predicate
    deterministically -- `predicates` is typically a set) and each predicate's
    salient tokens extracted once. Reused across every content word, so the sort
    and tokenization don't repeat per token."""
    return [(pred, _predicate_salient_tokens(pred)) for pred in sorted(predicates)]


def _best_predicate_for_token(token: str, pred_index) -> str | None:
    """The predicate whose salient token best matches ``token`` (exact beats a
    longer shared stem), or None -- one predicate per query word, so a repeated
    relation word yields a repeated hop (multiplicity, not a set). ``pred_index``
    is the ``_predicate_token_index`` list (pre-sorted; a strict-improvement test
    keeps the first = lexicographically-smallest predicate on a tie)."""
    best_pred, best_score = None, -1
    for pred, ptoks in pred_index:
        for pt in ptoks:
            if _stem_match(token, pt):
                score = 1000 if token == pt else _common_prefix_len(token, pt)
                if score > best_score:
                    best_pred, best_score = pred, score
    return best_pred


def _find_anchor(query: str, entity_names) -> tuple[str | None, int]:
    """The longest entity name occurring (case-insensitive, TOKEN-BOUNDED) in the
    query. Returns (anchor_surface, char_index_of_name) or (None, -1).

    Token-bounded only (the padded `" name "` match) -- no bare-substring fallback,
    which would ground the WRONG anchor ("Acme" inside "Acmeville"); `_norm` already
    turns punctuation into spaces, so a real mention is space-delimited. Ranking is
    fully deterministic (longest name, then earliest occurrence, then lexicographic
    via the sorted scan), so a set of `entity_names` can't make routing order-dependent."""
    ql = f" {_norm(query)} "
    best_key = None  # (-len, name_start)
    best_name, best_start = None, -1
    for name in sorted(entity_names or ()):
        n = _norm(name)
        if not n:
            continue
        idx = ql.find(f" {n} ")
        if idx == -1:
            continue
        name_start = idx + 1  # skip the leading pad space -> the name's own offset
        key = (-len(n), name_start)
        if best_key is None or key < best_key:
            best_key, best_name, best_start = key, name, name_start
    return best_name, best_start


#: Relation markers: an unmapped content word immediately followed by one of these
#: is a relation we failed to ground ("spouse OF", "authored BY"), so the chain is
#: incomplete and we must abstain rather than truncate to a wrong node. Kept to the
#: two HIGH-SIGNAL prepositions on purpose: "of"/"by" almost always follow a
#: relation, whereas "to"/"in"/"at"/"for" also head ordinary verb complements
#: ("leads TO", "results IN") and would over-abstain. A filler noun ("the film
#: Inception") is followed by the ENTITY, not a marker, so it does NOT trip this.
#: Residual boundary: a missed relation followed by a pronoun/conjunction ("directed
#: X and produced it") -- that needs the embedding / LLMQueryClassifier tier, not a
#: token rule; abstaining-or-answering there is no worse than today's synthesis path.
_REL_MARKERS = frozenset({"of", "by"})


def _extract_nl_chain_slots(query: str, predicates, entity_names):
    """(anchor, relation_chain) from a natural-language multi-hop question, or
    (None, None). Grounded in the slice vocab. The returned order is a proximity
    HINT -- the caller's walk validates it against the graph (only the ordering
    that reaches a terminal completes), so a wrong hint is self-correcting."""
    if not predicates or not entity_names:
        return None, None
    anchor, anchor_pos = _find_anchor(query, entity_names)
    if anchor is None:
        return None, None
    ql = f" {_norm(query)} "
    anchor_norm = _norm(anchor)
    # Blank the anchor span so its own words can't be mistaken for a relation.
    if anchor_pos >= 0:
        ql = ql[:anchor_pos] + (" " * len(anchor_norm)) + ql[anchor_pos + len(anchor_norm):]
    # Full token stream (positions + next-token, for the relation-marker guard).
    toks = ql.split(" ")
    stream: list[tuple[int, str, str]] = []  # (char_pos, tok, next_tok)
    pos = 0
    for i, tok in enumerate(toks):
        nxt = toks[i + 1] if i + 1 < len(toks) else ""
        if tok:
            stream.append((pos, tok, nxt))
        pos += len(tok) + 1
    is_content = lambda t: len(t) >= 4 and t not in _REL_STOPWORDS  # noqa: E731
    content = [(p, t, nx) for p, t, nx in stream if is_content(t)]

    # Map EACH content word to its best predicate (one hop per word) -- so a
    # repeated relation word ("the employer of the employer of X") yields a
    # repeated hop. hits carries the ordered per-occurrence relations.
    pred_index = _predicate_token_index(predicates)  # sort + tokenize once, not per word
    hits: list[tuple[int, str]] = []  # (distance-from-anchor, predicate), per occurrence
    covered: set[int] = set()         # indices into `content` that grounded to a predicate
    for ci, (cp, ct, _nx) in enumerate(content):
        pred = _best_predicate_for_token(ct, pred_index)
        if pred is None:
            continue
        covered.add(ci)
        hits.append((abs(cp - max(anchor_pos, 0)), pred))
    if not hits:
        return None, None
    # COMPLETENESS GUARD: abstain if an UNMAPPED content word sits in a relation
    # position (immediately before "of"/"by") -- that's a hop we couldn't ground
    # (e.g. "spouse of" with no stem to "married_to"). Firing the partial chain
    # would walk short and return a WRONG intermediate node (the None-fallthrough
    # can't catch a chain that completes early). Abstaining routes to today's
    # retrieval+synthesis path -- never worse than the status quo. Filler nouns
    # ("the film Inception") are NOT before a marker, so they don't trip this; the
    # uncovered-synonym case is the documented boundary (needs the embed/LLM tier).
    for ci, (_cp, _ct, nx) in enumerate(content):
        if ci not in covered and nx in _REL_MARKERS:
            return None, None
    # proximity-to-anchor order is the first-tried HINT (the walk validates it).
    # Keep every occurrence -- multiplicity matters (a relation can repeat in a
    # chain), so do NOT dedup.
    hits.sort(key=lambda h: h[0])
    return anchor, tuple(pred for _dist, pred in hits)


def classify_query(query: str, *, predicates=None, entity_names=None) -> QueryProfile:
    """Heuristic intent + slot extraction. `predicates` is the slice's stored predicate
    ids (underscored); `entity_names` is the slice's entity canonical names. Both ground
    the template-free NL multi-hop chain extractor -- when they yield an anchor + relation
    chain, a natural-language multi-hop question routes to the deterministic `chain` walk
    instead of LLM synthesis-over-the-ball. When either is absent, behavior is unchanged."""
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
    if intent is QueryIntent.MULTI_HOP:
        anchor, chain = _extract_chain_slots(query)  # engineered template
        if anchor and chain:
            return QueryProfile(intent=intent, anchor_surface=anchor,
                                relation_chain=chain, confidence=0.9)
    # Template-free NL chain: fires for MULTI_HOP and LOOKUP ("Who is the spouse of the
    # director of X?" classifies as LOOKUP). Grounded + conservative; trace_chain's
    # None-fallthrough makes a mis-parse degrade to the general path, never a wrong answer.
    if intent in (QueryIntent.MULTI_HOP, QueryIntent.LOOKUP):
        anchor, chain = _extract_nl_chain_slots(query, predicates, entity_names)
        if anchor and chain:
            return QueryProfile(intent=QueryIntent.MULTI_HOP, anchor_surface=anchor,
                                relation_chain=chain, confidence=0.85, chain_ordered=False)
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
        if profile.confidence >= MIN_CONF and profile.anchor_surface and profile.relation_chain:
            return RetrievalPlan(mode="chain")  # relation-guided deterministic walk
        return RetrievalPlan(mode="hybrid")
    return RetrievalPlan(mode="local")  # LOOKUP + low-confidence fallbacks


class QueryClassifier(Protocol):
    def classify(self, query: str, *, predicates=None) -> QueryProfile: ...


def resolve_profile(query: str, *, predicates=None, entity_names=None,
                    llm_classifier: QueryClassifier | None = None) -> QueryProfile:
    """Two-tier: heuristic FIRST; escalate to the injected classifier ONLY when the heuristic is
    below MIN_CONF AND a classifier is given; the classifier's result wins only if strictly more
    confident (so a confidently-abstaining tier-2 keeps the heuristic -> safe local route)."""
    h = classify_query(query, predicates=predicates, entity_names=entity_names)
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
