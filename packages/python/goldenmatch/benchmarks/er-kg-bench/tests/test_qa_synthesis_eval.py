"""Synthesis-given-gold eval -- wheel-free (stub LLM; operates on subgraph dicts, no PyStore)."""
from __future__ import annotations

from erkgbench.qa_e2e.synthesis_eval import (
    SynthesisGivenGold,
    evaluate_synthesis_given_gold,
    render_md,
)


class _StubLLM:
    def complete(self, prompt: str) -> str:
        return "some answer"


def test_evaluate_shape():
    r = evaluate_synthesis_given_gold(llm=_StubLLM(), seed=7, n_questions=6, ambiguity=0.0)
    assert r.n > 0
    assert 0.0 <= r.mean <= 1.0
    assert all(0.0 <= v <= 1.0 for v in r.by_hop.values())
    assert r.n_failed == 0  # stub never raises


def test_render_md():
    md = render_md(SynthesisGivenGold(mean=0.42, by_hop={1: 0.8, 2: 0.3}, n=20, n_failed=0),
                   model="qwen2.5:7b-instruct")
    assert "synthesis-given-gold" in md.lower() and "0.420" in md and "1-hop 0.80" in md
