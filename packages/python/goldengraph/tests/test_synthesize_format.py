"""Synthesis formatting: name-keyed edges + step-by-step path-tracing prompt.

The 2026-06-22 probes showed goldengraph's multi-hop answer was IN the retrieved
subgraph but went unread -- the old `subj_id -pred-> obj_id` dump made the LLM join
ids to names to trace a chain. These pure (no native, no LLM) tests pin the new
contract: edges read as `Name -[rel]-> Name`, and the prompt instructs step-by-step
relation following.
"""

from __future__ import annotations

from goldengraph.synthesize import _format_subgraph, synthesize_local
from conftest import RecordingLLM

_SUB = {
    "entities": [
        {"entity_id": 0, "canonical_name": "Acme", "typ": "org"},
        {"entity_id": 1, "canonical_name": "Rocket", "typ": "product"},
    ],
    "edges": [{"subj": 0, "predicate": "made", "obj": 1}],
}


def test_edges_are_name_keyed_not_id_keyed():
    text = _format_subgraph(_SUB)
    assert "Acme -[made]-> Rocket" in text
    # the bare numeric-id edge form is gone
    assert "0 -made-> 1" not in text


def test_local_prompt_instructs_step_by_step_tracing():
    llm = RecordingLLM()
    synthesize_local("Following made from Acme, what is reached?", _SUB, llm)
    prompt = llm.prompts[-1]
    assert "step by step" in prompt.lower()
    assert "Acme -[made]-> Rocket" in prompt
    assert "Answer:" in prompt
