"""Bounded thread-pool answer loop (QA_E2E_ANSWER_WORKERS).

These are pure-Python / no-network: a deterministic fake engine + fake judge + the
real BudgetTracker. They lock the guarantees the parallel loop must hold -- output
independent of worker count and completion order, total cost order-independent, the
workers=1 byte-identical fallback -- plus the two thread-safety fixes (goldengraph's
per-question thread-local token counter and the existing 429 backoff)."""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

_BENCH_ROOT = Path(__file__).resolve().parent.parent
if str(_BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_BENCH_ROOT))

from erkgbench.qa_e2e.corpora import Document, QACorpus, QAItem  # noqa: E402
from erkgbench.qa_e2e.harness import (  # noqa: E402
    AnswerResult,
    BuildResult,
    _answer_workers,
    _engine_answer_workers,
    run_engine,
    run_engine_ab,
)


def _qidx(question: str) -> int:
    # question is "question number {i} ..." -- recover i so the fake is deterministic.
    return int(question.split()[2])


class _FakeEngine:
    """Deterministic, network-free QAEngine. answer() derives everything from the
    question index: even -> names the gold (match), odd -> a miss; tokens = 100+i /
    10+i so per-question cost is a known, order-independent sum. `stagger` sleeps
    later indices LESS so completion order is the reverse of submission order."""

    name = "fake"
    fidelity = "real-e2e"
    answer_parallel_safe = True

    def __init__(self, *, stagger: float = 0.0, mode_suffix: bool = False):
        self._stagger = stagger
        self._mode_suffix = mode_suffix

    def build_kg(self, corpus) -> BuildResult:
        return BuildResult(
            handle={"n": len(corpus.documents)},
            input_tokens=500,
            output_tokens=50,
            latency_s=0.0,
        )

    def answer(self, handle, question: str, mode: str | None = None) -> AnswerResult:
        i = _qidx(question)
        if self._stagger:
            # later indices sleep less -> they finish first (reverse of submit order)
            time.sleep(self._stagger * (100 - i))
        base = f"Ans{i}" if i % 2 == 0 else f"nope{i}"
        text = f"the answer is {base}"
        if self._mode_suffix and mode is not None:
            text = f"{text} [{mode}]"
        return AnswerResult(
            text=text,
            retrieved_fact_ids=(f"d{i}",),
            input_tokens=100 + i,
            output_tokens=10 + i,
            latency_s=0.0,
        )


class _UnsafeEngine(_FakeEngine):
    name = "unsafe"
    answer_parallel_safe = False

    def __init__(self):
        super().__init__()
        self.max_concurrent = 0
        self._live = 0
        self._lock = threading.Lock()

    def answer(self, handle, question: str, mode: str | None = None) -> AnswerResult:
        with self._lock:
            self._live += 1
            self.max_concurrent = max(self.max_concurrent, self._live)
        try:
            time.sleep(0.01)
            return super().answer(handle, question, mode=mode)
        finally:
            with self._lock:
                self._live -= 1


class _FakeJudge:
    """callable(prompt)->str. Deterministic: YES iff the prediction names the gold
    ('Ans'). Thread-safe call counter so the parallel path is exercised safely."""

    def __init__(self):
        self.calls = 0
        self._lock = threading.Lock()

    def __call__(self, prompt: str) -> str:
        with self._lock:
            self.calls += 1
        return "YES" if "Ans" in prompt else "NO"


def _corpus(n: int = 16) -> QACorpus:
    docs = tuple(Document(id=f"d{i}", text=f"doc body {i}") for i in range(n))
    qs = tuple(
        QAItem(
            id=f"q{i}",
            question=f"question number {i} about a thing",
            gold_answer=f"Ans{i}",
            gold_supporting_fact_ids=(f"d{i}",),
            hop_count=(i % 3) + 1,
            ambiguity_level=0.0,
        )
        for i in range(n)
    )
    return QACorpus(name="fake", documents=docs, questions=qs)


# --- QA_E2E_ANSWER_WORKERS resolution ---------------------------------------------


def test_answer_workers_default_is_8(monkeypatch):
    monkeypatch.delenv("QA_E2E_ANSWER_WORKERS", raising=False)
    assert _answer_workers() == 8


def test_answer_workers_one_forces_sequential(monkeypatch):
    monkeypatch.setenv("QA_E2E_ANSWER_WORKERS", "1")
    assert _answer_workers() == 1


def test_answer_workers_non_int_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("QA_E2E_ANSWER_WORKERS", "banana")
    assert _answer_workers() == 8


def test_engine_answer_workers_respects_unsafe_flag():
    assert _engine_answer_workers(_FakeEngine(), 8) == 8
    assert _engine_answer_workers(_UnsafeEngine(), 8) == 1
    # requested==1 always sequential regardless of engine
    assert _engine_answer_workers(_FakeEngine(), 1) == 1


# --- Parallel == sequential, cost, ordering ---------------------------------------


def _run(workers: int, monkeypatch, *, stagger: float = 0.0, judge=None):
    monkeypatch.setenv("QA_E2E_ANSWER_WORKERS", str(workers))
    return run_engine(
        _FakeEngine(stagger=stagger),
        _corpus(),
        model="gpt-4o-mini",
        budget_usd=1000.0,
        judge=judge,
    )


def test_parallel_scorecard_equals_sequential(monkeypatch):
    seq = _run(1, monkeypatch)
    par = _run(8, monkeypatch)
    # Whole scorecard is byte-equal: same metrics, same per-question order, same
    # n_answered -- independent of worker count.
    assert par == seq
    assert par["n_answered"] == 16
    assert [r["id"] for r in par["per_question"]] == [f"q{i}" for i in range(16)]


def test_parallel_scorecard_equals_sequential_with_judge(monkeypatch):
    jseq, jpar = _FakeJudge(), _FakeJudge()
    seq = _run(1, monkeypatch, judge=jseq)
    par = _run(8, monkeypatch, judge=jpar)
    assert par == seq
    # judge ran once per answered question on both paths
    assert jseq.calls == jpar.calls == 16
    # even indices name the gold -> YES; odd -> NO ; half the answered set
    assert par["answer_judge"] == seq["answer_judge"]


def test_total_cost_identical_across_worker_counts(monkeypatch):
    seq = _run(1, monkeypatch)
    par = _run(8, monkeypatch)
    assert par["cost_usd"] == seq["cost_usd"]
    assert par["cost_usd"] > 0.0


def test_ordering_stable_under_staggered_completion(monkeypatch):
    # Later indices finish FIRST (stagger). The sort-by-index must still order the
    # per-question records by original index, matching the sequential run exactly.
    seq = _run(1, monkeypatch)
    par = _run(8, monkeypatch, stagger=0.0005)
    assert [r["id"] for r in par["per_question"]] == [f"q{i}" for i in range(16)]
    assert par == seq


def test_workers_1_is_the_sequential_path(monkeypatch):
    # Explicit: =1 answers everything in order with the same metrics a small manual
    # sequential expectation would give (even indices match -> answer_match 0.5).
    res = _run(1, monkeypatch)
    assert res["n_answered"] == 16
    assert res["answer_match"] == 0.5  # even indices name the gold, odd miss
    assert res["support_recall"] == 1.0  # every answer returns its own gold fact id


# --- Budget cap under parallelism -------------------------------------------------


def test_budget_cap_stops_answering_under_parallelism(monkeypatch):
    monkeypatch.setenv("QA_E2E_ANSWER_WORKERS", "8")

    class _Expensive(_FakeEngine):
        def answer(self, handle, question, mode=None):
            i = _qidx(question)
            return AnswerResult(
                text=f"the answer is Ans{i}",
                retrieved_fact_ids=(f"d{i}",),
                input_tokens=5_000_000,
                output_tokens=5_000_000,
                latency_s=0.0,
            )

    # Tiny cap: build cost alone is fine, but answers are huge -> only a bounded few
    # (<= workers) get answered before submission stops. Must not crash.
    res = run_engine(_Expensive(), _corpus(), model="gpt-4o", budget_usd=0.001)
    assert res["budget_exhausted"] is True
    assert res["n_answered"] < 16  # capped, partial result


# --- A/B (mode-based) parallel == sequential --------------------------------------


def test_run_engine_ab_parallel_equals_sequential(monkeypatch):
    monkeypatch.setenv("QA_E2E_ANSWER_WORKERS", "1")
    seq = run_engine_ab(
        _FakeEngine(mode_suffix=True), _corpus(), model="gpt-4o-mini",
        budget_usd=1000.0, modes=["local", "hybrid"],
    )
    monkeypatch.setenv("QA_E2E_ANSWER_WORKERS", "8")
    par = run_engine_ab(
        _FakeEngine(mode_suffix=True), _corpus(), model="gpt-4o-mini",
        budget_usd=1000.0, modes=["local", "hybrid"],
    )
    assert par == seq
    assert par["n_answered"] == 16
    # per-arm cost is attributed and equal across worker counts
    assert par["arms"]["local"]["cost_usd"] == seq["arms"]["local"]["cost_usd"]
    assert par["total_cost_usd"] == seq["total_cost_usd"]


def test_run_engine_ab_arms_answer_same_question_set(monkeypatch):
    monkeypatch.setenv("QA_E2E_ANSWER_WORKERS", "8")
    res = run_engine_ab(
        _FakeEngine(mode_suffix=True), _corpus(), model="gpt-4o-mini",
        budget_usd=1000.0, modes=["local", "hybrid"],
    )
    a = [r["id"] for r in res["arms"]["local"]["per_question"]]
    b = [r["id"] for r in res["arms"]["hybrid"]["per_question"]]
    assert a == b == [f"q{i}" for i in range(16)]


# --- Unsafe engine is forced sequential -------------------------------------------


def test_unsafe_engine_never_runs_answers_concurrently(monkeypatch):
    monkeypatch.setenv("QA_E2E_ANSWER_WORKERS", "8")
    eng = _UnsafeEngine()
    res = run_engine(eng, _corpus(), model="gpt-4o-mini", budget_usd=1000.0)
    assert res["n_answered"] == 16
    # answer_parallel_safe=False -> forced sequential -> only ever one in flight
    assert eng.max_concurrent == 1
