"""Run loop + cost cap for the QA-e2e head-to-head. One QAEngine per system;
the harness builds the KG once, answers every question, scores via metrics, and
enforces a hard USD cap via goldenmatch's BudgetTracker."""
from __future__ import annotations

import json
import os
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


def _localize_trace(engine, handle, corpus, *, limit: int = 10) -> None:
    """Diagnostic: for each question, classify WHERE the gold answer is lost --
    extraction (gold entity never made it into the graph), retrieval (it's in the
    graph but the seed-walk didn't surface it), or synthesis (it was retrieved but
    the LLM wrote a wrong answer). Opt-in via GOLDENGRAPH_QA_TRACE; only engines
    exposing `localize` (goldengraph) participate. Uses the same token-containment
    as answer_match so "in graph/ball" lines up with the headline scoring."""
    print(f"== localize trace (first {limit}; where is the answer lost?) ==", flush=True)
    for q in corpus.questions[:limit]:
        try:
            loc = engine.localize(handle, q.question)
        except Exception as exc:  # diagnostic must never break the scored run
            print(f"  [{q.id}] localize failed: {exc!r}", flush=True)
            continue
        in_graph = bool(metrics.answer_match(" ".join(loc["graph_names"]), q.gold_answer))
        in_wide = bool(metrics.answer_match(" ".join(loc.get("wide_names", [])), q.gold_answer))
        in_ball = bool(metrics.answer_match(" ".join(loc["retrieved_names"]), q.gold_answer))
        if not in_graph:
            stage = "EXTRACTION (gold not a graph node: never extracted, or a non-entity answer)"
        elif not in_wide:
            stage = "RETRIEVAL-BROKEN-CHAIN (in graph but unreachable from the seeds)"
        elif not in_ball:
            stage = "RETRIEVAL-BUDGET (reachable from seeds but outside the budget-capped ball)"
        else:
            stage = "SYNTHESIS (retrieved, wrong answer written)"
        print(
            f"  [{q.id}] hop{q.hop_count} gold={q.gold_answer!r} "
            f"in_graph={in_graph} in_wide={in_wide} in_ball={in_ball} -> {stage}",
            flush=True,
        )
        print(
            f"      seeds={loc['seed_names']} graph={loc['n_graph_entities']}ent "
            f"wide={loc.get('n_wide_entities', '?')}ent "
            f"ball={loc['n_retrieved_entities']}ent/{loc['n_retrieved_edges']}edges",
            flush=True,
        )
        comps = loc.get("component_names")
        if comps is not None:
            seed_idx = loc.get("seed_component_idx", -1)
            ans_idx = next(
                (i for i, names in enumerate(comps)
                 if metrics.answer_match(" ".join(names), q.gold_answer)),
                -1,
            )
            same = ans_idx == seed_idx and ans_idx >= 0
            seed_sz = len(comps[seed_idx]) if seed_idx >= 0 else 0
            ans_sz = len(comps[ans_idx]) if ans_idx >= 0 else 0
            sizes = loc.get("component_sizes", [])
            print(
                f"      components: {loc.get('n_components', '?')} total "
                f"(top sizes {sizes[:6]}); seed_comp={seed_sz}ent "
                f"answer_comp={ans_sz}ent same_component={same}",
                flush=True,
            )


def run_engine(engine: QAEngine, corpus, *, model: str, budget_usd: float) -> dict:
    """Build the KG, answer every question under a hard cost cap, score, return a
    result dict. Stops cleanly (partial result) when the budget is exhausted."""
    tracker = BudgetTracker(BudgetConfig(max_cost_usd=budget_usd))

    build = engine.build_kg(corpus)
    tracker.record_usage(build.input_tokens, build.output_tokens, model)

    if os.environ.get("GOLDENGRAPH_QA_TRACE", "") not in ("", "0", "false") and hasattr(
        engine, "localize"
    ):
        _localize_trace(engine, build.handle, corpus)

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
