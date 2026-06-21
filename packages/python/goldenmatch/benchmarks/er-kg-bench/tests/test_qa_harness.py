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
    assert res["exact_match"] == 1.0
    assert res["support_recall"] == 1.0
    assert res["cost_usd"] > 0.0
    assert res["budget_exhausted"] is False


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
