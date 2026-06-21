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
    f1s: list[float] = []
    recalls: list[float] = []
    decay_rows: list[tuple[int, float]] = []
    answered = 0
    for q in corpus.questions:
        if tracker.budget_exhausted or not tracker.can_send(_estimate_tokens(q.question)):
            break
        ans = engine.answer(build.handle, q.question)
        tracker.record_usage(ans.input_tokens, ans.output_tokens, model)
        em = metrics.exact_match(ans.text, q.gold_answer)
        ems.append(em)
        f1s.append(metrics.token_f1(ans.text, q.gold_answer))
        recalls.append(
            metrics.supporting_fact_recall(ans.retrieved_fact_ids, q.gold_supporting_fact_ids)
        )
        decay_rows.append((q.hop_count, em))
        answered += 1

    return {
        "engine": engine.name,
        "fidelity": engine.fidelity,
        "corpus": corpus.name,
        "model": model,
        "n_questions": len(corpus.questions),
        "n_answered": answered,
        "exact_match": _mean(ems),
        "token_f1": _mean(f1s),
        "support_recall": _mean(recalls),
        "decay_curve": metrics.decay_curve(decay_rows),
        "cost_usd": round(tracker.total_cost_usd, 6),
        "budget_exhausted": tracker.budget_exhausted,
    }


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _mean(xs: list[float]) -> float:
    return round(sum(xs) / len(xs), 4) if xs else 0.0


def write_results(results: list[dict], *, md_path: str | Path, json_path: str | Path) -> None:
    """Write the headline markdown table + the raw JSON. ASCII only."""
    Path(json_path).write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
    lines = ["# ER-KG-Bench -- end-to-end multi-hop QA (evidence program #1)", ""]
    lines.append(
        "| engine | corpus | EM | token-F1 | support-recall | cost (USD) | answered | budget hit |"
    )
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in results:
        lines.append(
            f"| {r['engine']} | {r['corpus']} | {r['exact_match']} | {r['token_f1']} | "
            f"{r['support_recall']} | {r['cost_usd']} | {r['n_answered']}/{r['n_questions']} | "
            f"{'yes' if r['budget_exhausted'] else 'no'} |"
        )
    lines.append("")
    lines.append("## Decay curve (engineered corpus: mean EM by hop count)")
    for r in results:
        if r["corpus"] == "engineered":
            curve = ", ".join(f"{h}:{v}" for h, v in r["decay_curve"].items())
            lines.append(f"- {r['engine']}: {curve}")
    Path(md_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
