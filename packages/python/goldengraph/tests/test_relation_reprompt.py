"""GOLDENGRAPH_RELATION_REPROMPT: a 2nd pass that adds relations among the given entities."""
from goldengraph.extract import Mention
from goldengraph.relation_reprompt import relation_reprompt, relation_reprompt_enabled


class _CaptureLLM:
    """Records the prompt; returns a fixed relationships JSON (rel 0->1)."""
    def __init__(self, payload='{"relationships": [{"subj": 0, "predicate": "founded_by", "obj": 1}]}'):
        self.prompt = None
        self.payload = payload

    def complete(self, prompt):
        self.prompt = prompt
        return self.payload
    # no complete_json -> _complete_extraction falls back to .complete


def _mentions():
    return [Mention(name="Amazon", typ="org"), Mention(name="Jeff Bezos", typ="person")]


def test_gate_enabled(monkeypatch):
    monkeypatch.delenv("GOLDENGRAPH_RELATION_REPROMPT", raising=False)
    assert relation_reprompt_enabled() is False
    monkeypatch.setenv("GOLDENGRAPH_RELATION_REPROMPT", "1")
    assert relation_reprompt_enabled() is True
    for off in ("", "0", "False", "off", " no "):
        monkeypatch.setenv("GOLDENGRAPH_RELATION_REPROMPT", off)
        assert relation_reprompt_enabled() is False, off


def test_prompt_lists_entities(monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_EXTRACT_JSON_MODE", "0")  # force .complete on the stub
    llm = _CaptureLLM()
    relation_reprompt("Amazon was founded by Jeff Bezos.", _mentions(), llm)
    assert "0: Amazon (org)" in llm.prompt
    assert "1: Jeff Bezos (person)" in llm.prompt


def test_parses_and_maps_indices(monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_EXTRACT_JSON_MODE", "0")
    rels = relation_reprompt("t", _mentions(), _CaptureLLM())
    assert len(rels) == 1
    assert (rels[0].subj, rels[0].predicate, rels[0].obj) == (0, "founded_by", 1)


def test_drops_out_of_range_and_self_loops(monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_EXTRACT_JSON_MODE", "0")
    payload = ('{"relationships": ['
               '{"subj": 0, "predicate": "x", "obj": 5},'    # obj out of range (n=2)
               '{"subj": 1, "predicate": "y", "obj": 1},'    # self-loop
               '{"subj": 0, "predicate": "ok", "obj": 1}]}')  # valid
    rels = relation_reprompt("t", _mentions(), _CaptureLLM(payload))
    assert [(r.subj, r.predicate, r.obj) for r in rels] == [(0, "ok", 1)]


def test_malformed_json_returns_empty(monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_EXTRACT_JSON_MODE", "0")
    assert relation_reprompt("t", _mentions(), _CaptureLLM("not json")) == []


def test_empty_mentions_no_llm_call():
    class _Boom:
        def complete(self, prompt):
            raise AssertionError("must not be called on empty mentions")
    assert relation_reprompt("t", [], _Boom()) == []


def test_vocab_instruction_prepended(monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_EXTRACT_JSON_MODE", "0")
    llm = _CaptureLLM()
    relation_reprompt("t", _mentions(), llm, relation_vocab=["founded_by", "works_at"])
    assert "founded_by, works_at" in llm.prompt        # .format(vocab=...) applied, not raw {vocab}
    assert "{vocab}" not in llm.prompt


def _identity_resolver():
    from goldengraph.resolve import ResolvedEntity

    def resolver(mentions):
        return [
            ResolvedEntity(local_id=i, canonical_name=m.name, typ=m.typ,
                           surface_names=[m.name], record_keys=[], member_idx=[i])
            for i, m in enumerate(mentions)
        ]
    return resolver


def test_prepare_doc_appends_reprompt_edges_only_when_gated(monkeypatch):
    import importlib

    from goldengraph.extract import Extraction, Mention, Relationship
    ingest = importlib.import_module("goldengraph.ingest")  # __init__ shadows the submodule name

    calls = {"n": 0}

    def base_extractor(text, llm=None):
        return Extraction(mentions=[Mention(name="Amazon", typ="org"),
                                    Mention(name="Jeff Bezos", typ="person")],
                          relationships=[])

    def fake_reprompt(text, mentions, llm, *, relation_vocab=None):
        calls["n"] += 1
        return [Relationship(subj=0, predicate="founded_by", obj=1)]

    monkeypatch.setattr(ingest, "relation_reprompt", fake_reprompt)
    monkeypatch.delenv("GOLDENGRAPH_CHUNK_EXTRACT", raising=False)
    resolver = _identity_resolver()

    # gate OFF -> no reprompt, no edges
    monkeypatch.delenv("GOLDENGRAPH_RELATION_REPROMPT", raising=False)
    ex, _, _ = ingest._prepare_doc("t", llm=None, resolver=resolver, profile_fps=False,
                                   extractor=base_extractor)
    assert calls["n"] == 0 and len(ex.relationships) == 0

    # gate ON -> reprompt called once, edge appended
    monkeypatch.setenv("GOLDENGRAPH_RELATION_REPROMPT", "1")
    ex2, _, _ = ingest._prepare_doc("t", llm=None, resolver=resolver, profile_fps=False,
                                    extractor=base_extractor)
    assert calls["n"] == 1 and len(ex2.relationships) == 1


def test_prepare_doc_reprompt_raise_preserves_first_pass(monkeypatch):
    import importlib

    from goldengraph.extract import Extraction, Mention, Relationship
    ingest = importlib.import_module("goldengraph.ingest")

    def base_extractor(text, llm=None):
        return Extraction(mentions=[Mention(name="A", typ="org"), Mention(name="B", typ="org")],
                          relationships=[Relationship(subj=0, predicate="rel", obj=1)])

    def boom(text, mentions, llm, *, relation_vocab=None):
        raise RuntimeError("reprompt exploded")

    monkeypatch.setattr(ingest, "relation_reprompt", boom)
    monkeypatch.setenv("GOLDENGRAPH_RELATION_REPROMPT", "1")
    monkeypatch.delenv("GOLDENGRAPH_CHUNK_EXTRACT", raising=False)
    ex, ents, _ = ingest._prepare_doc("t", llm=None, resolver=_identity_resolver(),
                                      profile_fps=False, extractor=base_extractor)
    # first-pass entities + edge survive; NOT the empty-extraction fallback
    assert len(ex.mentions) == 2 and len(ex.relationships) == 1 and len(ents) == 2
