"""Synthesis formatting: name-keyed edges + multi-hop decomposition prompt.

The 2026-06-22 probes showed goldengraph's multi-hop answer was IN the retrieved
subgraph but went unread -- the old `subj_id -pred-> obj_id` dump made the LLM join
ids to names to trace a chain. After the hop-clamp fix isolated the Politburo miss to
SYNTHESIS (answer retrieved, chain unwalked), the prompt was upgraded to instruct
explicit multi-hop decomposition into sub-questions with bridge entities carried
forward. These pure (no native, no LLM) tests pin the new contract: edges read as
`Name -[rel]-> Name`, and the prompt instructs multi-hop sub-question chaining.
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


def test_local_prompt_instructs_multihop_decomposition():
    llm = RecordingLLM()
    synthesize_local("Following made from Acme, what is reached?", _SUB, llm)
    prompt = llm.prompts[-1]
    # The prompt must steer the model to decompose a multi-hop question into a
    # chain of sub-questions and carry bridge entities forward (the Politburo
    # SYNTHESIS miss: answer retrieved, chain unwalked).
    assert "multi-hop" in prompt.lower()
    assert "sub-question" in prompt.lower()
    assert "Acme -[made]-> Rocket" in prompt
    assert "Answer:" in prompt


def test_local_prompt_anchors_on_seed_names():
    llm = RecordingLLM()
    synthesize_local("q?", _SUB, llm, seed_names=["Acme", "Acme", "Rocket"])
    prompt = llm.prompts[-1]
    # Seeds are surfaced as anchor entities (deduped) so the walk starts at the
    # query-relevant nodes rather than guessing among the whole ball.
    assert "Anchor entities: Acme, Rocket" in prompt
    # And the model is forced to commit to a named entity, not a description.
    assert "EXACT name" in prompt


def test_local_prompt_seed_names_optional():
    llm = RecordingLLM()
    synthesize_local("q?", _SUB, llm)
    assert "Anchor entities:" in llm.prompts[-1]  # falls back to a placeholder line
