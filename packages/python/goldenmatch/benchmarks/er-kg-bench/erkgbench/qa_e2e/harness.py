"""Run loop + cost cap for the QA-e2e head-to-head. One QAEngine per system;
the harness builds the KG once, answers every question, scores via metrics, and
enforces a hard USD cap via goldenmatch's BudgetTracker."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from goldenmatch.config.schemas import BudgetConfig
from goldenmatch.core.llm_budget import BudgetTracker

from . import metrics


@dataclass(frozen=True)
class BuildResult:
    handle: Any
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0


@dataclass(frozen=True)
class AnswerResult:
    text: str
    retrieved_fact_ids: tuple[str, ...] = ()
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0


@runtime_checkable
class QAEngine(Protocol):
    name: str
    fidelity: str

    def build_kg(self, corpus) -> BuildResult: ...
    def answer(self, handle, question: str) -> AnswerResult: ...


def run_engine(engine: QAEngine, corpus, *, model: str, budget_usd: float) -> dict:
    """Build the KG, answer every question under a hard cost cap, score, return a
    result dict. Stops cleanly (partial result) when the budget is exhausted."""
    tracker = BudgetTracker(BudgetConfig(max_cost_usd=budget_usd))

    build = engine.build_kg(corpus)
    tracker.record_usage(build.input_tokens, build.output_tokens, model)

    ems: list[float] = []
    matches: list[float] = []
    f1s: list[float] = []
    recalls: list[float] = []
    decay_rows: list[tuple[int, float]] = []
    records: list[dict] = []
    answered = 0
    for q in corpus.questions:
        if tracker.budget_exhausted or not tracker.can_send(_estimate_tokens(q.question)):
            break
        ans = engine.answer(build.handle, q.question)
        tracker.record_usage(ans.input_tokens, ans.output_tokens, model)
        am = metrics.answer_match(ans.text, q.gold_answer)
        em = metrics.exact_match(ans.text, q.gold_answer)
        f1 = metrics.token_f1(ans.text, q.gold_answer)
        rec = metrics.supporting_fact_recall(ans.retrieved_fact_ids, q.gold_supporting_fact_ids)
        matches.append(am)
        ems.append(em)
        f1s.append(f1)
        recalls.append(rec)
        # Decay = correctness by hop count. answer_match (containment) is the
        # meaningful correctness signal for free-text answers; exact_match reads ~0.
        decay_rows.append((q.hop_count, am))
        # Persist the per-question record so a near-zero aggregate is debuggable
        # post-hoc (wrong reasoning vs. async-corrupted output vs. phrasing the
        # matcher misses) -- the aggregate alone made the bench a black box. The
        # prediction is truncated so a verbose engine can't bloat the artifact.
        records.append(
            {
                "id": q.id,
                "question": q.question,
                "gold_answer": q.gold_answer,
                "prediction": _truncate(ans.text),
                "hop_count": q.hop_count,
                "answer_match": am,
                "exact_match": em,
                "token_f1": round(f1, 4),
            }
        )
        answered += 1

    return {
        "engine": engine.name,
        "fidelity": engine.fidelity,
        "corpus": corpus.name,
        "model": model,
        # The corpus is single-ambiguity per run; record it so an ambiguity sweep can be
        # aggregated into the decay curve (0.0 for MuSiQue / empty corpora).
        "ambiguity": corpus.questions[0].ambiguity_level if corpus.questions else 0.0,
        "n_questions": len(corpus.questions),
        "n_answered": answered,
        "answer_match": _mean(matches),
        "exact_match": _mean(ems),
        "token_f1": _mean(f1s),
        "support_recall": _mean(recalls),
        "decay_curve": metrics.decay_curve(decay_rows),
        "cost_usd": round(tracker.total_cost_usd, 6),
        "budget_exhausted": tracker.budget_exhausted,
        "per_question": records,
    }


def _truncate(text: str, limit: int = 2000) -> str:
    """Cap a stored prediction so a verbose engine (LightRAG essays) can't bloat
    the results artifact; the head is what the answer_match check reads anyway."""
    if len(text) <= limit:
        return text
    return text[:limit] + " ...[truncated]"


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _mean(xs: list[float]) -> float:
    return round(sum(xs) / len(xs), 4) if xs else 0.0


def write_results(results: list[dict], *, md_path: str | Path, json_path: str | Path) -> None:
    """Write the headline markdown table + the raw JSON. ASCII only."""
    Path(json_path).write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
    lines = ["# ER-KG-Bench -- end-to-end multi-hop QA (evidence program #1)", ""]
    lines.append(
        "| engine | corpus | answer-match | EM | token-F1 | support-recall | "
        "cost (USD) | answered | budget hit |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for r in results:
        lines.append(
            f"| {r['engine']} | {r['corpus']} | {r['answer_match']} | {r['exact_match']} | "
            f"{r['token_f1']} | {r['support_recall']} | {r['cost_usd']} | "
            f"{r['n_answered']}/{r['n_questions']} | "
            f"{'yes' if r['budget_exhausted'] else 'no'} |"
        )
    lines.append("")
    lines.append("## Decay curve (engineered corpus: mean answer-match by hop count)")
    for r in results:
        if r["corpus"] == "engineered":
            curve = ", ".join(f"{h}:{v}" for h, v in r["decay_curve"].items())
            lines.append(f"- {r['engine']}: {curve}")
    Path(md_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
