"""Template-free natural-language multi-hop chain routing (wheel-free).

The engineered `_CHAIN_RE` template ("Starting from X, follow the relation R1,
then R2.") was the ONLY way a question reached the deterministic LLM-free
`trace_chain` walk; real questions ("Who is married to the person who directed
Inception?") fell through to LLM synthesis over the ball (measured: gold-chain
synthesis 1.00 vs ball synthesis ~0.15). `route._extract_nl_chain_slots` recovers
(anchor, relation_chain) from natural language, grounded in the slice's entity +
predicate vocabularies, so those questions now route to `chain`.

The contract these tests pin: FIRE on verb-form multi-hop questions and walk to
the right node; ABSTAIN (never return a wrong intermediate node) when the
question carries relational structure that can't be grounded (a pure noun synonym
with no shared stem, an unknown anchor, no relation at all). Abstaining just
routes to today's retrieval+synthesis path -- never worse than the status quo.
"""

from __future__ import annotations

from goldengraph.answer import (
    _slice_entity_names,
    _slice_predicates,
    _trace_chain_any_order,
    ask,
    trace_chain,
)
from goldengraph.route import QueryIntent, classify_query, plan_query


class _StubGraph:
    """Minimal wheel-free slice: seeds_by_name / query / entities, mirroring the
    `test_chain_retrieval` convention."""

    def __init__(self, entities, edges):
        self._ents = entities
        self._edges = edges
        self._byname: dict = {}
        for e in entities:
            self._byname.setdefault(e["canonical_name"], []).append(e["entity_id"])

    def entities(self):
        return list(self._ents)

    def seeds_by_name(self, name):
        return list(self._byname.get(name, []))

    def query(self, ids, hops):
        ids = set(ids)
        edges = [e for e in self._edges if e["subj"] in ids or e["obj"] in ids]
        keep = ids | {e["subj"] for e in edges} | {e["obj"] for e in edges}
        return {"entities": [e for e in self._ents if e["entity_id"] in keep], "edges": edges}


def _film_graph():
    # Inception -directed by-> Christopher Nolan -married to-> Emma Thomas
    ents = [
        {"entity_id": 0, "canonical_name": "Inception"},
        {"entity_id": 1, "canonical_name": "Christopher Nolan"},
        {"entity_id": 2, "canonical_name": "Emma Thomas"},
        {"entity_id": 3, "canonical_name": "Interstellar"},  # distractor entity
    ]
    edges = [
        {"subj": 0, "predicate": "directed by", "obj": 1},
        {"subj": 1, "predicate": "married to", "obj": 2},
        {"subj": 3, "predicate": "directed by", "obj": 1},  # distractor edge
    ]
    return _StubGraph(ents, edges)


def _profile(query, g):
    return classify_query(
        query, predicates=_slice_predicates(g), entity_names=_slice_entity_names(g)
    )


# --- FIRES: verb-form NL routes to a chain and walks to the right node ---------

def test_nl_verb_two_hop_fires_and_walks():
    g = _film_graph()
    p = _profile("Who is married to the person who directed Inception?", g)
    assert p.intent is QueryIntent.MULTI_HOP
    assert p.anchor_surface == "Inception"
    assert p.relation_chain == ("directed by", "married to")
    assert plan_query(p).mode == "chain"
    assert trace_chain(g, p.anchor_surface, p.relation_chain) == "Emma Thomas"


def test_nl_of_chain_conjunction_form_fires():
    g = _film_graph()
    p = _profile("Inception was directed by whom, and who are they married to?", g)
    assert p.relation_chain == ("directed by", "married to")
    assert trace_chain(g, p.anchor_surface, p.relation_chain) == "Emma Thomas"


def test_nl_single_hop_verb_fires():
    g = _film_graph()
    p = _profile("Who directed Inception?", g)
    assert p.relation_chain == ("directed by",)
    assert plan_query(p).mode == "chain"
    assert trace_chain(g, p.anchor_surface, p.relation_chain) == "Christopher Nolan"


def test_nl_stem_bridges_morphology():
    # "director" (noun) grounds to the "directed by" predicate via the shared stem.
    g = _film_graph()
    p = _profile("Who directed the film Inception?", g)
    assert p.relation_chain == ("directed by",)


def test_nl_walk_order_is_anchor_proximity():
    # Acme -authored-> Book -located in-> Paris. The walk must take `authored` first
    # (adjacent to the anchor) then `located in`; the reversed order dies at hop 1.
    ents = [
        {"entity_id": 0, "canonical_name": "Acme"},
        {"entity_id": 1, "canonical_name": "Book"},
        {"entity_id": 2, "canonical_name": "Paris"},
    ]
    edges = [
        {"subj": 0, "predicate": "authored", "obj": 1},
        {"subj": 1, "predicate": "located in", "obj": 2},
    ]
    g = _StubGraph(ents, edges)
    p = _profile("Where is the thing authored by Acme located?", g)
    assert p.anchor_surface == "Acme"
    # both relations recovered (order is a hint); the walk validates order via the
    # graph -- the wrong order ("located in" first) has no edge from Acme and dies.
    assert set(p.relation_chain) == {"authored", "located in"}
    assert p.chain_ordered is False
    assert _trace_chain_any_order(g, p.anchor_surface, p.relation_chain) == "Paris"


def test_nl_repeated_relation_keeps_multiplicity():
    # Acme -works_at-> Beta -works_at-> Gamma: the chain REPEATS a relation. The
    # extractor must emit works_at TWICE (a set would collapse to a 1-hop walk and
    # answer "Beta"); the walk then reaches Gamma.
    ents = [
        {"entity_id": 0, "canonical_name": "Acme"},
        {"entity_id": 1, "canonical_name": "Beta"},
        {"entity_id": 2, "canonical_name": "Gamma"},
    ]
    edges = [
        {"subj": 0, "predicate": "works_at", "obj": 1},
        {"subj": 1, "predicate": "works_at", "obj": 2},
    ]
    g = _StubGraph(ents, edges)
    p = _profile("For Acme, works at and works at leads to which entity?", g)
    assert p.relation_chain == ("works_at", "works_at")
    assert _trace_chain_any_order(g, p.anchor_surface, p.relation_chain) == "Gamma"


def test_order_tolerant_trusts_hint_when_it_completes():
    # A -r1-> B -r2-> D (the hint order) AND A -r2-> C -r1-> E (an alternative that
    # also completes). The hint order encodes the question's phrasing, so it wins
    # -> D, not an abstain and not E.
    ents = [{"entity_id": i, "canonical_name": n} for i, n in
            enumerate(["A", "B", "D", "C", "E"])]
    edges = [
        {"subj": 0, "predicate": "r1", "obj": 1},   # A -r1-> B
        {"subj": 1, "predicate": "r2", "obj": 2},    # B -r2-> D  (hint completes)
        {"subj": 0, "predicate": "r2", "obj": 3},    # A -r2-> C
        {"subj": 3, "predicate": "r1", "obj": 4},    # C -r1-> E  (alt completes)
    ]
    g = _StubGraph(ents, edges)
    assert _trace_chain_any_order(g, "A", ("r1", "r2")) == "D"


def test_order_tolerant_abstains_on_ambiguous_fallback():
    # The hint order (r1 first) can't start -- A has no r1 edge -- and the two
    # completing fallbacks disagree ([r2,r1,r3]->D vs [r3,r1,r2]->G), so the parse
    # is genuinely ambiguous: abstain (None) rather than guess.
    ents = [{"entity_id": i, "canonical_name": n} for i, n in
            enumerate(["A", "B", "C", "D", "E", "F", "G"])]
    edges = [
        {"subj": 0, "predicate": "r2", "obj": 1},   # A -r2-> B
        {"subj": 1, "predicate": "r1", "obj": 2},   # B -r1-> C
        {"subj": 2, "predicate": "r3", "obj": 3},   # C -r3-> D   => [r2,r1,r3] -> D
        {"subj": 0, "predicate": "r3", "obj": 4},   # A -r3-> E
        {"subj": 4, "predicate": "r1", "obj": 5},   # E -r1-> F
        {"subj": 5, "predicate": "r2", "obj": 6},   # F -r2-> G   => [r3,r1,r2] -> G
    ]
    g = _StubGraph(ents, edges)
    assert _trace_chain_any_order(g, "A", ("r1", "r2", "r3")) is None


# --- ABSTAINS: never emit a chain that would answer wrong ----------------------

def test_nl_noun_synonym_abstains():
    # "spouse" has no shared stem with "married to" -> the second hop can't ground.
    # Firing the partial chain would walk one hop and return "Christopher Nolan"
    # (WRONG); the completeness guard must abstain instead.
    g = _film_graph()
    p = _profile("Who is the spouse of the director of Inception?", g)
    assert p.relation_chain is None
    assert plan_query(p).mode != "chain"


class _SynEmbedder:
    """Deterministic 3-D stub: 'spouse' ~ 'married to'; 'friend' orthogonal to both
    predicates. Lets the semantic bridge be tested without a real model."""

    _V = {
        "married to": [1.0, 0.0, 0.0],
        "directed by": [0.0, 1.0, 0.0],
        "spouse": [0.95, 0.05, 0.0],   # cosine ~0.998 with "married to"
        "friend": [0.0, 0.0, 1.0],     # cosine 0 with both predicates
    }

    def embed(self, texts):
        return [self._V.get(t, [0.0, 0.0, 0.0]) for t in texts]


def test_nl_synonym_bridged_by_embedder():
    # "spouse" has no shared stem with "married to", so it abstains without an
    # embedder (test_nl_noun_synonym_abstains). WITH one, the semantic bridge grounds
    # it and the full 2-hop chain fires -> Emma Thomas.
    g = _film_graph()
    p = classify_query(
        "Who is the spouse of the director of Inception?",
        predicates=_slice_predicates(g),
        entity_names=_slice_entity_names(g),
        embedder=_SynEmbedder(),
    )
    assert p.relation_chain == ("directed by", "married to")
    assert plan_query(p).mode == "chain"
    assert _trace_chain_any_order(g, p.anchor_surface, p.relation_chain) == "Emma Thomas"


def test_nl_broken_embedder_length_mismatch_abstains():
    # An embedder that returns the wrong number of vectors is a contract violation;
    # the bridge must treat it as a failure (abstain), not let zip silently drop
    # candidates and fabricate a partial chain.
    class _BadEmbedder:
        def embed(self, texts):
            return [[1.0, 0.0, 0.0]]  # always length 1, regardless of input count

    g = _film_graph()
    p = classify_query(
        "Who is the spouse of the director of Inception?",
        predicates=_slice_predicates(g),
        entity_names=_slice_entity_names(g),
        embedder=_BadEmbedder(),
    )
    assert p.relation_chain is None


def test_nl_embedder_low_similarity_still_abstains():
    # An unmapped relation word that embeds FAR from every predicate ("friend")
    # must NOT be force-bridged -- the cosine floor keeps it uncovered, so the
    # guard still abstains rather than fabricate a hop.
    g = _film_graph()
    p = classify_query(
        "Who is the friend of the director of Inception?",
        predicates=_slice_predicates(g),
        entity_names=_slice_entity_names(g),
        embedder=_SynEmbedder(),
    )
    assert p.relation_chain is None


def test_embed_bridge_min_env_parsing_and_clamping(monkeypatch):
    # The cosine floor is read from GOLDENGRAPH_NL_EMBED_BRIDGE_MIN. A misconfigured
    # env var must never silently disable the bridge (nan) or make it bind anything
    # (a negative / out-of-range floor): non-float and non-finite fall back to the
    # 0.55 default, and any finite value is clamped into the meaningful [0, 1] range.
    from goldengraph.route import _embed_bridge_min

    # unset -> default
    monkeypatch.delenv("GOLDENGRAPH_NL_EMBED_BRIDGE_MIN", raising=False)
    assert _embed_bridge_min() == 0.55

    # a valid in-range value passes through untouched
    monkeypatch.setenv("GOLDENGRAPH_NL_EMBED_BRIDGE_MIN", "0.7")
    assert _embed_bridge_min() == 0.7

    # non-float -> default (not a crash)
    monkeypatch.setenv("GOLDENGRAPH_NL_EMBED_BRIDGE_MIN", "not-a-number")
    assert _embed_bridge_min() == 0.55

    # nan would make every cosine comparison False and silently disable the bridge
    monkeypatch.setenv("GOLDENGRAPH_NL_EMBED_BRIDGE_MIN", "nan")
    assert _embed_bridge_min() == 0.55

    # inf is non-finite -> default
    monkeypatch.setenv("GOLDENGRAPH_NL_EMBED_BRIDGE_MIN", "inf")
    assert _embed_bridge_min() == 0.55

    # a negative floor would bind anything -> clamp up to 0.0
    monkeypatch.setenv("GOLDENGRAPH_NL_EMBED_BRIDGE_MIN", "-0.3")
    assert _embed_bridge_min() == 0.0

    # above 1.0 is not a meaningful cosine floor -> clamp down to 1.0
    monkeypatch.setenv("GOLDENGRAPH_NL_EMBED_BRIDGE_MIN", "1.5")
    assert _embed_bridge_min() == 1.0


def test_nl_unknown_anchor_abstains():
    g = _film_graph()
    p = _profile("Who directed Titanic?", g)  # Titanic is not in the slice
    assert p.anchor_surface is None
    assert p.relation_chain is None


def test_nl_pure_lookup_abstains():
    g = _film_graph()
    p = _profile("What is Inception?", g)  # anchor present, no relation
    assert p.relation_chain is None


def test_nl_anchor_is_token_bounded_not_substring():
    # An entity name must not ground on a SUBSTRING of a larger token ("Acme"
    # inside "Acmeville") -- that would pick the wrong anchor. Token-bounded only.
    ents = [{"entity_id": 0, "canonical_name": "Acme"}]
    edges = [{"subj": 0, "predicate": "located in", "obj": 0}]
    g = _StubGraph(ents, edges)
    p = _profile("Where is Acmeville located?", g)
    assert p.anchor_surface is None  # "Acme" is not a whole token in "Acmeville"
    assert p.relation_chain is None


def test_nl_unrelated_content_word_abstains():
    # anchor present + one groundable relation, but "famous"/"movie" are ungrounded
    # content words -> the guard abstains rather than risk a truncated read.
    g = _film_graph()
    p = _profile("Tell me about the famous movie Inception", g)
    assert p.relation_chain is None


def test_nl_extraction_is_noop_without_vocab():
    # Backward compat: no entity_names -> the NL extractor cannot ground an anchor,
    # so classify_query behaves exactly as before (no chain from free NL).
    p = classify_query("Who is married to the person who directed Inception?")
    assert p.relation_chain is None


def test_template_form_still_fires_unchanged():
    # No regression to the engineered path.
    p = classify_query(
        "Starting from Inception, follow the relation directed by, then married to. "
        "What entity do you reach?"
    )
    assert p.anchor_surface == "Inception"
    assert p.relation_chain == ("directed by", "married to")
    assert p.confidence >= 0.8


# --- end-to-end through ask(mode="auto") --------------------------------------

class _StubStore:
    def __init__(self, g):
        self._g = g

    def as_of(self, valid_t, tx_t):
        return self._g


class _UnusedLLM:
    def complete(self, prompt):  # must NOT be called on the chain path
        raise AssertionError("LLM was invoked -- the NL chain path should be LLM-free")


class _UnusedEmbedder:
    def embed(self, texts):
        raise AssertionError("embedder was invoked on the chain path")


def test_ask_auto_routes_nl_question_to_llm_free_chain():
    g = _film_graph()
    out = ask(
        "Who is married to the person who directed Inception?",
        _StubStore(g),
        llm=_UnusedLLM(),
        embedder=_UnusedEmbedder(),
        valid_t=100,
        tx_t=100,
        mode="auto",
    )
    assert out == "Emma Thomas"
