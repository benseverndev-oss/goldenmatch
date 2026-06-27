"""Opt-in real-LLM answer arms -- stub-LLM, wheel-free."""
from __future__ import annotations

from erkgbench.qa_e2e import crossover as cx


class _StubLLM:
    def __init__(self, response):
        self.response = response
        self.prompts = []

    def complete(self, prompt):
        self.prompts.append(prompt)
        return self.response


def test_llm_answer_rag_maps_text_to_canonical():
    passages = ["X works at Apple.", "Apple is in Cupertino."]
    s2c = {"Apple": {"a"}, "Cupertino": {"c"}, "X": {"x"}}
    llm = _StubLLM("The answer is Cupertino.")
    got = cx.llm_answer_rag(passages, "where is X located?", llm, surface_to_canon=s2c)
    assert got == "c"
    assert "Apple is in Cupertino." in llm.prompts[-1]


def test_llm_answer_unknown_is_none():
    llm = _StubLLM("Some Bogus Entity")
    got = cx.llm_answer_rag(["irrelevant"], "q?", llm, surface_to_canon={"Apple": {"a"}})
    assert got is None
