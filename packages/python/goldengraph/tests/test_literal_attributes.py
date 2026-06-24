"""Literal/attribute-value capability (GOLDENGRAPH_LITERAL_ATTRS).

A KG that drops dates/quantities/amounts can't answer 'when'/'how much' questions
-- the dominant non-entity loss bucket in the 2026-06-23 N=50 MuSiQue trace. These
pure (no native, no LLM) tests pin the slice: extraction captures attributes, the
batch turns them into typed literal leaf nodes + edges, and synthesis is allowed
to answer with a literal value (only when the flag is on -- the entity-only path
stays byte-identical).
"""

from __future__ import annotations

import goldengraph.synthesize as synth
from goldengraph.extract import Attribute, Extraction, Mention, parse_extraction
from goldengraph.ingest import build_batch
from goldengraph.resolve import ResolvedEntity
from goldengraph.synthesize import _format_subgraph, synthesize_local
from conftest import RecordingLLM, StubLLM


# --- extraction -------------------------------------------------------------


def test_parse_extraction_reads_attributes():
    raw = (
        '{"entities": [{"name": "Acme", "type": "org"}],'
        ' "relationships": [],'
        ' "attributes": [{"subj": 0, "predicate": "founded on",'
        ' "value": "1 April 1976", "type": "date"}]}'
    )
    ex = parse_extraction(raw)
    assert len(ex.attributes) == 1
    a = ex.attributes[0]
    assert (a.subj, a.predicate, a.value, a.typ) == (0, "founded on", "1 April 1976", "date")


def test_parse_extraction_attributes_are_defensive():
    raw = (
        '{"entities": [{"name": "Acme", "type": "org"}], "relationships": [],'
        ' "attributes": ['
        '   {"subj": 9, "predicate": "p", "value": "x", "type": "date"},'   # bad index
        '   {"subj": 0, "predicate": "p", "value": "", "type": "date"},'    # empty value
        '   {"subj": 0, "predicate": "p", "value": true, "type": "date"},'  # non-scalar
        '   {"subj": 0, "predicate": "p", "value": 42, "type": "weird"}'    # coerced type
        ']}'
    )
    ex = parse_extraction(raw)
    assert len(ex.attributes) == 1
    assert ex.attributes[0].value == "42"
    assert ex.attributes[0].typ == "text"  # unknown type -> text


def test_parse_extraction_without_attributes_key_is_empty():
    ex = parse_extraction('{"entities": [], "relationships": []}')
    assert ex.attributes == []


def test_extract_literals_flag_uses_the_attribute_prompt():
    from goldengraph.extract import extract

    llm = RecordingLLM(response='{"entities": [], "relationships": [], "attributes": []}')
    extract("some text", llm, literals=True)
    assert "attributes" in llm.prompts[-1]
    assert '"date|quantity|text"' in llm.prompts[-1]
    # default (no literals) keeps the entity-only prompt
    llm2 = RecordingLLM(response='{"entities": [], "relationships": []}')
    extract("some text", llm2)
    assert "attributes" not in llm2.prompts[-1]


# --- batch building ---------------------------------------------------------


def _ent(local_id, name, typ="org", members=(0,)):
    return ResolvedEntity(
        local_id=local_id, canonical_name=name, typ=typ,
        surface_names=[name], record_keys=[f"k{local_id}"], member_idx=list(members),
    )


def test_build_batch_emits_literal_nodes_and_edges():
    entities = [_ent(0, "Acme", members=[0])]
    ex = Extraction(
        mentions=[Mention("Acme", "org")],
        relationships=[],
        attributes=[Attribute(subj=0, predicate="founded on", value="1976", typ="date")],
    )
    batch = build_batch(ex, entities, at=1)
    lits = [e for e in batch["entities"] if e["typ"].startswith("literal:")]
    assert len(lits) == 1
    lit = lits[0]
    assert lit["typ"] == "literal:date"
    assert lit["canonical_name"] == "1976"
    assert lit["record_keys"] == []  # never anchors a cross-doc merge
    # an edge connects the real entity to the literal leaf
    edge = [e for e in batch["edges"] if e["obj_local"] == lit["local_id"]]
    assert len(edge) == 1
    assert edge[0]["subj_local"] == 0 and edge[0]["predicate"] == "founded on"


def test_build_batch_dedupes_identical_literals_within_doc():
    entities = [_ent(0, "Acme", members=[0]), _ent(1, "Globex", members=[1])]
    ex = Extraction(
        mentions=[Mention("Acme", "org"), Mention("Globex", "org")],
        relationships=[],
        attributes=[
            Attribute(subj=0, predicate="founded on", value="1976", typ="date"),
            Attribute(subj=1, predicate="founded on", value="1976", typ="date"),
        ],
    )
    batch = build_batch(ex, entities, at=1)
    lits = [e for e in batch["entities"] if e["typ"].startswith("literal:")]
    assert len(lits) == 1  # one shared "1976" date node
    # but both entities get an edge to it
    assert sum(1 for e in batch["edges"] if e["obj_local"] == lits[0]["local_id"]) == 2


def test_build_batch_no_attributes_is_unchanged():
    entities = [_ent(0, "Acme", members=[0])]
    ex = Extraction(mentions=[Mention("Acme", "org")], relationships=[])
    batch = build_batch(ex, entities, at=1)
    assert all(not e["typ"].startswith("literal:") for e in batch["entities"])
    assert batch["edges"] == []


# --- synthesis --------------------------------------------------------------

_SUB_LITERAL = {
    "entities": [
        {"entity_id": 0, "canonical_name": "Acme", "typ": "org"},
        {"entity_id": 1, "canonical_name": "1976", "typ": "literal:date"},
    ],
    "edges": [{"subj": 0, "predicate": "founded on", "obj": 1}],
}


def test_format_subgraph_quotes_literal_values():
    text = _format_subgraph(_SUB_LITERAL)
    assert '"1976" (date value)' in text  # entity-list label
    assert 'Acme -[founded on]-> "1976"' in text  # quoted in the edge chain


def test_synthesize_uses_literal_prompt_only_when_flag_on(monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_LITERAL_ATTRS", "1")
    llm = RecordingLLM(response="Answer: 1976")
    synthesize_local("When founded?", _SUB_LITERAL, llm)
    assert "literal VALUE leaf" in llm.prompts[-1]

    monkeypatch.setenv("GOLDENGRAPH_LITERAL_ATTRS", "0")
    llm2 = RecordingLLM(response="Answer: Acme")
    synthesize_local("q?", _SUB_LITERAL, llm2)
    assert "literal VALUE leaf" not in llm2.prompts[-1]
    assert "ALWAYS a single entity" in llm2.prompts[-1]  # entity-only path intact


def test_synthesize_returns_literal_answer():
    llm = StubLLM("hop one\nAnswer: 1976")
    assert synthesize_local("When founded?", _SUB_LITERAL, llm) == "1976"


def test_entity_only_prompt_is_byte_identical_to_pre_literal():
    # The flag-off prompt must equal head + entity-clause + tail exactly, so the
    # measured entity-only baseline is never perturbed by this slice.
    assert synth._LOCAL_PROMPT == synth._LOCAL_HEAD + synth._ANSWER_ENTITY + synth._LOCAL_TAIL
