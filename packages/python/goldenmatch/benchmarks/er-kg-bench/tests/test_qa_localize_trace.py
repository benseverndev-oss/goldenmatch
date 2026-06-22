"""Harness localize-trace unit test -- offline. A fake engine exposes `localize`
returning canned graph/ball name sets; we assert run_engine's trace block (gated
by GOLDENGRAPH_QA_TRACE) classifies each question's loss as extraction /
retrieval / synthesis using the same containment as answer_match. No LLM, no
native store -- the classification logic is what we're locking down."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from erkgbench.qa_e2e.corpora import Document, QACorpus, QAItem  # noqa: E402
from erkgbench.qa_e2e.harness import AnswerResult, BuildResult, run_engine  # noqa: E402


class _FakeEngine:
    """Per-question canned localization: maps question id -> (graph, ball) name
    sets. answer() echoes a fixed wrong answer so scoring still runs."""

    name = "fake"
    fidelity = "test"

    def __init__(self, table):
        self._table = table
        self._qid_by_question = {}

    def build_kg(self, corpus) -> BuildResult:
        self._qid_by_question = {q.question: q.id for q in corpus.questions}
        return BuildResult(handle={"ok": True})

    def answer(self, handle, question: str) -> AnswerResult:
        return AnswerResult(text="some wrong answer")

    def localize(self, handle, question: str) -> dict:
        qid = self._qid_by_question[question]
        graph_names, wide_names, ball_names = self._table[qid]
        return {
            "seed_names": ["Seed"],
            "graph_names": graph_names,
            "wide_names": wide_names,
            "retrieved_names": ball_names,
            "n_graph_entities": len(graph_names),
            "n_wide_entities": len(wide_names),
            "n_retrieved_entities": len(ball_names),
            "n_retrieved_edges": 0,
        }


def _q(qid: str, gold: str) -> QAItem:
    return QAItem(
        id=qid,
        question=f"q for {qid}?",
        gold_answer=gold,
        gold_supporting_fact_ids=(),
        hop_count=2,
        ambiguity_level=0.0,
    )


def _corpus() -> QACorpus:
    qs = (
        _q("extraction_miss", "Exeter College"),
        _q("broken_chain", "the Politburo"),
        _q("budget_miss", "Lyon"),
        _q("synthesis_miss", "Genesis"),
    )
    return QACorpus(name="musique", documents=(Document(id="d", text="x"),), questions=qs)


def test_trace_classifies_four_loss_stages(monkeypatch, capsys):
    monkeypatch.setenv("GOLDENGRAPH_QA_TRACE", "1")
    # table: qid -> (graph_names, wide_names, ball_names)
    table = {
        # gold absent from graph entirely -> EXTRACTION
        "extraction_miss": (["Oriel College", "Oxford"], ["Oriel College"], ["Oriel College"]),
        # gold in graph, NOT reachable from seeds (not in wide) -> BROKEN-CHAIN
        "broken_chain": (["the Politburo", "Soviet Union"], ["Soviet Union"], ["Soviet Union"]),
        # gold in graph AND wide (reachable) but outside the ball -> BUDGET
        "budget_miss": (["Lyon", "France"], ["Lyon", "France"], ["France"]),
        # gold in the retrieved ball, answer still wrong -> SYNTHESIS
        "synthesis_miss": (["Genesis", "Nintendo"], ["Genesis", "Nintendo"], ["Genesis", "Nintendo"]),
    }
    run_engine(_FakeEngine(table), _corpus(), model="gpt-4o-mini", budget_usd=5.0)
    out = capsys.readouterr().out
    assert "localize trace" in out
    ex = next(ln for ln in out.splitlines() if "[extraction_miss]" in ln)
    assert "in_graph=False" in ex and "-> EXTRACTION" in ex
    bc = next(ln for ln in out.splitlines() if "[broken_chain]" in ln)
    assert "in_graph=True in_wide=False" in bc and "-> RETRIEVAL-BROKEN-CHAIN" in bc
    bg = next(ln for ln in out.splitlines() if "[budget_miss]" in ln)
    assert "in_graph=True in_wide=True in_ball=False" in bg and "-> RETRIEVAL-BUDGET" in bg
    sy = next(ln for ln in out.splitlines() if "[synthesis_miss]" in ln)
    assert "in_ball=True" in sy and "-> SYNTHESIS" in sy


def test_trace_off_by_default(monkeypatch, capsys):
    monkeypatch.delenv("GOLDENGRAPH_QA_TRACE", raising=False)
    run_engine(_FakeEngine({}), _corpus(), model="gpt-4o-mini", budget_usd=5.0)
    assert "localize trace" not in capsys.readouterr().out
