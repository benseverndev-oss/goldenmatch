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
