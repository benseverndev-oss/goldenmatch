"""Same-run local-vs-auto A/B plumbing (no LLM). Locks: one shared build, both arms
answer the same questions, per-mode scorecards + a legible delta -- so the paid
headline that measures the default-path chain routing is CI-validated end to end."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from erkgbench.qa_e2e.corpora import Document, QACorpus, QAItem  # noqa: E402
from erkgbench.qa_e2e.harness import AnswerResult, BuildResult, run_engine_ab  # noqa: E402


class _ModeVaryingEngine:
    name = "mock-ab"
    fidelity = "self-test"

    def __init__(self):
        self.builds = 0

    def build_kg(self, corpus) -> BuildResult:
        self.builds += 1  # the A/B must build EXACTLY once
        return BuildResult(handle={"n": len(corpus.documents)}, input_tokens=500, output_tokens=50)

    def answer(self, handle, question: str, mode: str | None = None) -> AnswerResult:
        # auto names the gold, local does not -> the two arms score differently.
        text = "Ada" if mode == "auto" else "nope"
        return AnswerResult(text=text, retrieved_fact_ids=("d1",), input_tokens=100, output_tokens=10)


def _toy_corpus(n=3):
    return QACorpus(
        name="toy",
        documents=(Document(id="d1", text="Acme was founded by Ada."),),
        questions=tuple(
            QAItem(
                id=f"q{i}",
                question="Who founded Acme?",
                gold_answer="Ada",
                gold_supporting_fact_ids=("d1",),
                hop_count=1,
                ambiguity_level=0.0,
            )
            for i in range(n)
        ),
    )


def test_ab_builds_once_and_splits_arms():
    engine = _ModeVaryingEngine()
    res = run_engine_ab(
        engine, _toy_corpus(3), model="gpt-4o-mini", budget_usd=25.0, modes=["local", "auto"]
    )
    assert engine.builds == 1  # ONE shared graph, not one per arm
    assert set(res["arms"]) == {"local", "auto"}
    # Both arms answered the SAME number of questions (aligned A/B).
    assert res["arms"]["local"]["n_answered"] == res["arms"]["auto"]["n_answered"] == 3
    assert res["n_answered"] == 3
    # auto names the gold -> 1.0; local does not -> 0.0. The delta surfaces the win.
    assert res["arms"]["auto"]["answer_match"] == 1.0
    assert res["arms"]["local"]["answer_match"] == 0.0
    assert res["comparison"]["answer_match"]["delta"] == 1.0
    # Per-arm answer cost is attributed; the shared build cost is separate.
    assert res["build_cost_usd"] > 0.0
    assert res["total_cost_usd"] > 0.0


def test_ab_rejects_duplicate_modes():
    # Duplicate modes collapse the arms dict into one -> a misleading one-arm "A/B".
    # run_engine_ab must reject them rather than silently mislead.
    import pytest

    engine = _ModeVaryingEngine()
    with pytest.raises(ValueError, match="unique"):
        run_engine_ab(
            engine, _toy_corpus(2), model="gpt-4o-mini", budget_usd=25.0,
            modes=["local", "local"],
        )


def test_ab_budget_cap_keeps_arms_aligned():
    # A tiny budget stops the loop, but a question is only started when BOTH arms can
    # be afforded -> the arms never desync (equal n_answered), even mid-truncation.
    engine = _ModeVaryingEngine()
    res = run_engine_ab(
        engine, _toy_corpus(50), model="gpt-4o", budget_usd=0.02, modes=["local", "auto"]
    )
    assert res["arms"]["local"]["n_answered"] == res["arms"]["auto"]["n_answered"]
    assert res["n_answered"] <= 50
