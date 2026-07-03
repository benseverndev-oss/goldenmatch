"""Opt-in real-LLM RAG aggregation row -- stub-LLM, wheel-free (no real key)."""
from __future__ import annotations

from erkgbench.qa_e2e.aggregation import llm_rag_aggregate
from erkgbench.qa_e2e.corpora import Document


class _StubLLM:
    def __init__(self, response):
        self.response = response
        self.prompts = []

    def complete(self, prompt):
        self.prompts.append(prompt)
        return self.response


def _docs(anchor, members):
    return tuple(
        Document(id=f"gm:a::rel::gm:m{i}", text=f"{anchor} rel {m}.",
                 src_surface=anchor, dst_surface=m)
        for i, m in enumerate(members)
    )


def test_llm_rag_maps_names_to_canonical_and_caps_window():
    docs = _docs("Acme", [f"M{i}" for i in range(20)])
    s2c = {f"M{i}": f"gm:m{i}" for i in range(20)}
    s2c["Acme"] = "gm:a"
    # LLM "lists" three known members + a bogus line
    llm = _StubLLM("M0\n- M1\nM2\nNotAnEntity")
    got = llm_rag_aggregate(docs, {"Acme"}, "rel", passage_k=10,
                            surface_to_canon=s2c, llm=llm)
    assert got == {"gm:m0", "gm:m1", "gm:m2"}  # known names mapped, bogus dropped
    # the prompt only carried the first passage_k docs
    assert llm.prompts[-1].count("rel") <= 10 + 1  # <=10 passages (+ the instruction)


def test_llm_rag_excludes_the_anchor():
    docs = _docs("Acme", ["M0"])
    s2c = {"M0": "gm:m0", "Acme": "gm:a"}
    llm = _StubLLM("Acme\nM0")  # model wrongly includes the anchor
    got = llm_rag_aggregate(docs, {"Acme"}, "rel", passage_k=10,
                            surface_to_canon=s2c, llm=llm)
    assert got == {"gm:m0"}
