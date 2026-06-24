from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from erkgbench.qa_e2e.corpora import Document, QACorpus, QAItem  # noqa: E402
from erkgbench.qa_e2e.harness import AnswerResult, BuildResult, run_engine  # noqa: E402


class _MockEngine:
    name = "mock"
    fidelity = "real-e2e"

    def __init__(self, answer_text="Ada", in_tokens=100, out_tokens=10):
        self._t = answer_text
        self._in = in_tokens
        self._out = out_tokens

    def build_kg(self, corpus) -> BuildResult:
        return BuildResult(
            handle={"n": len(corpus.documents)},
            input_tokens=500,
            output_tokens=50,
            latency_s=0.0,
        )

    def answer(self, handle, question: str) -> AnswerResult:
        return AnswerResult(
            text=self._t,
            retrieved_fact_ids=("d1",),
            input_tokens=self._in,
            output_tokens=self._out,
            latency_s=0.0,
        )


def _toy_corpus():
    return QACorpus(
        name="toy",
        documents=(Document(id="d1", text="Acme was founded by Ada."),),
        questions=(
            QAItem(
                id="q1",
                question="Who founded Acme?",
                gold_answer="Ada",
                gold_supporting_fact_ids=("d1",),
                hop_count=1,
                ambiguity_level=0.0,
            ),
        ),
    )


def test_run_engine_scores_and_records_cost():
    res = run_engine(_MockEngine(), _toy_corpus(), model="gpt-4o-mini", budget_usd=25.0)
    assert res["engine"] == "mock"
    assert res["n_answered"] == 1
    assert res["answer_match"] == 1.0
    assert res["exact_match"] == 1.0
    assert res["support_recall"] == 1.0
    assert res["cost_usd"] > 0.0
    assert res["budget_exhausted"] is False


def test_run_engine_answer_match_scores_free_text_when_em_misses():
    # a generative answer that NAMES the gold but isn't string-equal: answer_match
    # credits it (1.0), exact_match does not (0.0) -- the wiring this PR adds.
    engine = _MockEngine(answer_text="Following the chain, the answer is Ada.")
    res = run_engine(engine, _toy_corpus(), model="gpt-4o-mini", budget_usd=25.0)
    assert res["answer_match"] == 1.0
    assert res["exact_match"] == 0.0
    # decay curve is driven by answer_match (correctness by hop), so the 1-hop
    # question registers as correct.
    assert res["decay_curve"] == {1: 1.0}


def test_run_engine_persists_per_question_records():
    # the harness keeps a per-question record so a near-zero aggregate is
    # debuggable post-hoc -- this is what was missing (artifacts were aggregate-only).
    engine = _MockEngine(answer_text="the final entity is Ada")
    res = run_engine(engine, _toy_corpus(), model="gpt-4o-mini", budget_usd=25.0)
    recs = res["per_question"]
    assert len(recs) == res["n_answered"] == 1
    r = recs[0]
    assert r["id"] == "q1"
    assert r["question"] == "Who founded Acme?"
    assert r["gold_answer"] == "Ada"
    assert r["prediction"] == "the final entity is Ada"  # the actual answer is kept
    assert r["hop_count"] == 1
    assert r["answer_match"] == 1.0  # containment credits the naming sentence
    assert r["exact_match"] == 0.0


def test_run_engine_truncates_long_prediction():
    from erkgbench.qa_e2e.harness import _truncate

    engine = _MockEngine(answer_text="x" * 5000)
    res = run_engine(engine, _toy_corpus(), model="gpt-4o-mini", budget_usd=25.0)
    pred = res["per_question"][0]["prediction"]
    assert pred.endswith("...[truncated]")
    assert len(pred) < 5000
    assert _truncate("short") == "short"


def test_run_engine_stops_at_budget_cap():
    # a tiny cap so the FIRST answer call is refused after build cost
    big = _MockEngine(in_tokens=10_000_000, out_tokens=10_000_000)
    res = run_engine(big, _toy_corpus(), model="gpt-4o", budget_usd=0.001)
    assert res["budget_exhausted"] is True
    assert res["n_answered"] == 0  # partial result, not a crash


def test_cli_self_test_writes_results(tmp_path):
    from erkgbench.qa_e2e.run_qa_e2e import main

    md = tmp_path / "RESULTS_QA_E2E.md"
    js = tmp_path / "results_qa_e2e.json"
    rc = main(
        [
            "--self-test",
            "--corpus",
            "engineered",
            "--max-questions",
            "5",
            "--out-md",
            str(md),
            "--out-json",
            str(js),
        ]
    )
    assert rc == 0
    assert md.exists() and js.exists()
    assert "end-to-end multi-hop QA" in md.read_text(encoding="utf-8")


def test_run_engine_llm_judge_metric_populates_when_judge_given():
    # A stub judge callable(prompt)->str drives the format-fair equivalence metric.
    # The harness must populate answer_judge (overall + entity-subset) + the
    # per-question record, and must pass the question into the judge prompt.
    seen = []

    def _judge(prompt):
        seen.append(prompt)
        return "YES"

    res = run_engine(
        _MockEngine(answer_text="Definitely Ada, no doubt."),
        _toy_corpus(), model="gpt-4o-mini", budget_usd=25.0, judge=_judge,
    )
    assert res["answer_judge"] == 1.0
    assert res["answer_judge_entity"] == 1.0  # 'Ada' classifies as an entity gold
    assert res["per_question"][0]["answer_judge"] == 1.0
    assert len(seen) == 1 and "Who founded Acme?" in seen[0]


def test_run_engine_judge_none_by_default():
    res = run_engine(_MockEngine(), _toy_corpus(), model="gpt-4o-mini", budget_usd=25.0)
    assert res["answer_judge"] is None
    assert res["answer_judge_entity"] is None
    assert res["per_question"][0]["answer_judge"] is None
