"""GOLDENGRAPH_SYNTH_SELECT node-disambiguation preamble (synthesis-precision experiment).

The measured synthesis miss is WRONG-NODE selection: the model returns a plausible
NEIGHBOR of the answer (group vs member, famous adjacent person vs body, related event
vs thing). The flag inserts `_SELECT_PREAMBLE` before the answer clause to force a
type-check + candidate enumeration. Default off MUST be byte-identical. Pure, no live LLM."""
from __future__ import annotations

from conftest import RecordingLLM

from goldengraph.synthesize import (
    _LOCAL_PROMPT,
    _SELECT_PREAMBLE,
    _local_prompt,
    _select_enabled,
    synthesize_local,
)

_SUB = {
    "entities": [{"entity_id": 0, "canonical_name": "Acme", "typ": "org"}],
    "edges": [],
}


def test_default_on_inserts_preamble(monkeypatch):
    # DEFAULT (unset) is now ON (2026-07-22 flip: measured +18.7% rel entity-subset).
    monkeypatch.delenv("GOLDENGRAPH_SYNTH_SELECT", raising=False)
    monkeypatch.delenv("GOLDENGRAPH_LITERAL_ATTRS", raising=False)
    assert _select_enabled() is True
    assert _SELECT_PREAMBLE in _local_prompt()
    assert _local_prompt() != _LOCAL_PROMPT


def test_explicit_off_is_byte_identical(monkeypatch):
    # `=0`/`false`/'' opt out -> the composed prompt is the pre-clause entity prompt, exactly.
    monkeypatch.delenv("GOLDENGRAPH_LITERAL_ATTRS", raising=False)
    for off in ("0", "false", ""):
        monkeypatch.setenv("GOLDENGRAPH_SYNTH_SELECT", off)
        assert _select_enabled() is False, off
        assert _local_prompt() == _LOCAL_PROMPT, off
        assert _SELECT_PREAMBLE not in _local_prompt(), off


def test_flag_on_inserts_preamble_before_answer_clause(monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_SYNTH_SELECT", "1")
    monkeypatch.delenv("GOLDENGRAPH_LITERAL_ATTRS", raising=False)
    p = _local_prompt()
    assert _SELECT_PREAMBLE in p
    # Ordering: the disambiguation preamble sits BEFORE the "Answer:" format clause,
    # which itself sits before the trailing Question line.
    assert p.index(_SELECT_PREAMBLE) < p.index("prefixed 'Answer: '") < p.index("Question: {q}")


def test_flag_on_reaches_the_actual_prompt(monkeypatch):
    # End-to-end through synthesize_local: the preamble is in the prompt the LLM sees.
    monkeypatch.setenv("GOLDENGRAPH_SYNTH_SELECT", "1")
    llm = RecordingLLM("Answer: Acme")
    assert synthesize_local("Who founded Acme?", _SUB, llm) == "Acme"
    assert len(llm.prompts) == 1
    assert _SELECT_PREAMBLE in llm.prompts[0]


def test_explicit_off_absent_from_actual_prompt(monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_SYNTH_SELECT", "0")
    llm = RecordingLLM("Answer: Acme")
    synthesize_local("Who founded Acme?", _SUB, llm)
    assert _SELECT_PREAMBLE not in llm.prompts[0]


def test_composes_with_literals_flag(monkeypatch):
    # SELECT + LITERAL_ATTRS both on: preamble present AND the literal answer clause used.
    monkeypatch.setenv("GOLDENGRAPH_SYNTH_SELECT", "1")
    monkeypatch.setenv("GOLDENGRAPH_LITERAL_ATTRS", "1")
    p = _local_prompt()
    assert _SELECT_PREAMBLE in p
    assert "literal VALUE leaf" in p  # the _ANSWER_LITERAL clause
