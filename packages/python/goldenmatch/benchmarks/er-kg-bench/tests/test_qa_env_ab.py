"""Same-graph env-A/B plumbing (no LLM). Locks: one shared build, every arm answers
the same questions under its own env-config (applied then restored), per-arm
scorecards + a legible delta -- so the paid headline that measures a env-gated knob
(e.g. GOLDENGRAPH_SYNTH_SAMPLES self-consistency voting) is CI-validated end to end
and free of the build-variance confound that made head_to_head unreliable."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from erkgbench.qa_e2e.corpora import Document, QACorpus, QAItem  # noqa: E402
from erkgbench.qa_e2e.harness import AnswerResult, BuildResult, run_engine_ab_env  # noqa: E402

_ENV = "GOLDENGRAPH_SYNTH_SAMPLES"


class _EnvVaryingEngine:
    name = "mock-env-ab"
    fidelity = "self-test"

    def __init__(self):
        self.builds = 0

    def build_kg(self, corpus) -> BuildResult:
        self.builds += 1  # the A/B must build EXACTLY once
        return BuildResult(handle={"n": len(corpus.documents)}, input_tokens=500, output_tokens=50)

    def answer(self, handle, question: str) -> AnswerResult:
        # The answer reads the env under test -- "5" names the gold, "1" does not, so
        # the two arms score differently. Note: NO mode= kwarg -- the knob is env-only.
        text = "Ada" if os.environ.get(_ENV) == "5" else "nope"
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


def _arms():
    return [(f"{_ENV}=1", {_ENV: "1"}), (f"{_ENV}=5", {_ENV: "5"})]


def test_env_ab_builds_once_and_splits_arms():
    engine = _EnvVaryingEngine()
    res = run_engine_ab_env(
        engine, _toy_corpus(3), model="gpt-4o-mini", budget_usd=25.0, arms=_arms()
    )
    assert engine.builds == 1  # ONE shared graph, not one per arm
    assert set(res["arms"]) == {f"{_ENV}=1", f"{_ENV}=5"}
    # Both arms answered the SAME number of questions (aligned A/B).
    assert res["arms"][f"{_ENV}=1"]["n_answered"] == res["arms"][f"{_ENV}=5"]["n_answered"] == 3
    assert res["n_answered"] == 3
    # =5 names the gold -> 1.0; =1 does not -> 0.0. The delta surfaces the win.
    assert res["arms"][f"{_ENV}=5"]["answer_match"] == 1.0
    assert res["arms"][f"{_ENV}=1"]["answer_match"] == 0.0
    assert res["comparison"]["answer_match"]["delta"] == 1.0
    # Per-arm answer cost is attributed; the shared build cost is separate.
    assert res["build_cost_usd"] > 0.0
    assert res["total_cost_usd"] > 0.0


def test_env_ab_restores_environ():
    # The arm env-overrides must not leak: after the run, the var is back to its prior
    # state (here: unset). This is the guarantee that arms don't contaminate each other.
    prior = os.environ.pop(_ENV, None)
    try:
        engine = _EnvVaryingEngine()
        run_engine_ab_env(engine, _toy_corpus(2), model="gpt-4o-mini", budget_usd=25.0, arms=_arms())
        assert _ENV not in os.environ  # restored to absent
    finally:
        if prior is not None:
            os.environ[_ENV] = prior


def test_env_ab_restores_prior_value():
    # A pre-existing value is restored EXACTLY, not deleted.
    os.environ[_ENV] = "sentinel"
    try:
        engine = _EnvVaryingEngine()
        run_engine_ab_env(engine, _toy_corpus(2), model="gpt-4o-mini", budget_usd=25.0, arms=_arms())
        assert os.environ[_ENV] == "sentinel"
    finally:
        os.environ.pop(_ENV, None)


def test_env_ab_rejects_duplicate_labels():
    # Duplicate labels collapse the arms dict into one -> a misleading one-arm "A/B".
    import pytest

    engine = _EnvVaryingEngine()
    with pytest.raises(ValueError, match="unique"):
        run_engine_ab_env(
            engine, _toy_corpus(2), model="gpt-4o-mini", budget_usd=25.0,
            arms=[(f"{_ENV}=1", {_ENV: "1"}), (f"{_ENV}=1", {_ENV: "1"})],
        )


def test_env_ab_budget_cap_keeps_arms_aligned():
    # A tiny budget stops the loop, but a question is only started when EVERY arm can
    # be afforded -> the arms never desync (equal n_answered), even mid-truncation.
    engine = _EnvVaryingEngine()
    res = run_engine_ab_env(
        engine, _toy_corpus(50), model="gpt-4o", budget_usd=0.02, arms=_arms()
    )
    assert res["arms"][f"{_ENV}=1"]["n_answered"] == res["arms"][f"{_ENV}=5"]["n_answered"]
    assert res["n_answered"] <= 50
