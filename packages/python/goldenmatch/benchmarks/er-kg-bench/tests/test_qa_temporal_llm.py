"""Opt-in real-LLM RAG temporal row -- stub-LLM, wheel-free (no real key)."""
from __future__ import annotations

from erkgbench.qa_e2e.corpora import Document
from erkgbench.qa_e2e.temporal import llm_temporal_rag


class _StubLLM:
    def __init__(self, response):
        self.response = response
        self.prompts = []

    def complete(self, prompt):
        self.prompts.append(prompt)
        return self.response


def _docs():
    return (
        Document(id="x::works_at::a::t1", text="As of 1, X works at Apple.",
                 src_surface="X", dst_surface="Apple"),
        Document(id="x::works_at::b::t5", text="From 5, X works at Google.",
                 src_surface="X", dst_surface="Google"),
    )


def test_llm_rag_maps_answer_to_canonical_and_sees_both_passages():
    s2c = {"X": "x", "Apple": "a", "Google": "b"}
    llm = _StubLLM("Apple")  # model answers the pre-correction value
    got = llm_temporal_rag(_docs(), {"X"}, "works_at", 3, llm, surface_to_canon=s2c)
    assert got == "a"
    # both dated passages were in the prompt (no enforced temporal slice)
    assert "Apple" in llm.prompts[-1] and "Google" in llm.prompts[-1]


def test_llm_rag_unknown_answer_is_none():
    s2c = {"X": "x", "Apple": "a"}
    llm = _StubLLM("- Some Bogus Entity")
    assert llm_temporal_rag(_docs(), {"X"}, "works_at", 3, llm, surface_to_canon=s2c) is None
