"""Orchestrate per-case benchmark execution."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Iterable

from infermap.engine import MapEngine

from .cases import Case
from .metrics import (
    MetricInput,
    extract_predictions,
    f1_per_case,
    mean_reciprocal_rank,
    top1_accuracy,
)
from .report import CaseResult

logger = logging.getLogger("infermap_bench.runner")


@dataclass
class RunOptions:
    min_confidence: float = 0.3
    sample_size: int = 500
    failure_budget: float = 0.10


class FailureBudgetExceededError(RuntimeError):
    """More than `failure_budget` fraction of cases failed — the engine is likely broken."""


def _make_engine(options: RunOptions) -> MapEngine:
    """Construct the engine. Extracted so tests can monkey-patch it."""
    return MapEngine(
        min_confidence=options.min_confidence,
        sample_size=options.sample_size,
        return_score_matrix=True,
    )


def _score_case(case: Case, engine: MapEngine) -> CaseResult:
    """Run the engine on one case and score it against expected mappings."""
    try:
        # Use map_schemas (not map) to pass pre-extracted SchemaInfo directly
        # without round-tripping through extract_schema. The round-trip would
        # re-infer dtypes from sample strings via polars and cause drift from
        # the TS runner, which uses mapSchemas directly.
        result = engine.map_schemas(
            case.source_schema,
            case.target_schema,
        )
    except Exception as exc:
        logger.warning("Engine raised on case %s: %s", case.id, exc)
        return CaseResult(
            case_id=case.id,
            category=case.category,
            subcategory=case.subcategory,
            difficulty=case.expected_difficulty,
            tags=list(case.tags),
            top1=0.0,
            f1=0.0,
            mrr=0.0,
            tp=0,
            fp=0,
            fn=len(case.expected.mappings),
            predictions=[],
            failed=True,
            failure_reason=f"{type(exc).__name__}: {exc}",
        )

    inp = MetricInput(
        source_fields=[f.name for f in case.source_schema.fields],
        target_fields=[f.name for f in case.target_schema.fields],
        expected_mappings=list(case.expected.mappings),
        expected_unmapped_source=list(case.expected.unmapped_source),
        expected_unmapped_target=list(case.expected.unmapped_target),
        actual_mappings=[
            {"source": m.source, "target": m.target, "confidence": m.confidence}
            for m in result.mappings
        ],
        score_matrix=result.score_matrix or {},
        min_confidence=engine.min_confidence,
    )
    tp, fp, fn = f1_per_case(inp)
    denom = 2 * tp + fp + fn
    case_f1 = (2 * tp) / denom if denom > 0 else 1.0
    return CaseResult(
        case_id=case.id,
        category=case.category,
        subcategory=case.subcategory,
        difficulty=case.expected_difficulty,
        tags=list(case.tags),
        top1=top1_accuracy(inp),
        f1=case_f1,
        mrr=mean_reciprocal_rank(inp),
        tp=tp,
        fp=fp,
        fn=fn,
        predictions=extract_predictions(inp),
        failed=False,
        failure_reason=None,
    )


def _abort_if_over_budget(failed_count: int, total_count: int, budget: float) -> None:
    """Raise FailureBudgetExceededError if failed fraction strictly exceeds budget."""
    if total_count == 0:
        return
    if failed_count / total_count > budget:
        raise FailureBudgetExceededError(
            f"{failed_count}/{total_count} cases failed "
            f"({failed_count / total_count:.1%}), exceeds budget of {budget:.0%}. "
            f"The engine is likely broken."
        )


def run_cases(cases: Iterable[Case], options: RunOptions | None = None) -> list[CaseResult]:
    """Run engine against every case, collect results, enforce failure budget."""
    opts = options or RunOptions()
    engine = _make_engine(opts)
    cases_list = list(cases)
    results: list[CaseResult] = []
    for case in cases_list:
        t0 = time.perf_counter()
        result = _score_case(case, engine)
        dt_ms = (time.perf_counter() - t0) * 1000
        logger.info("[bench] case %s: f1=%.3f duration_ms=%.1f",
                    case.id, result.f1, dt_ms)
        results.append(result)

    failed = sum(1 for r in results if r.failed)
    _abort_if_over_budget(failed, len(results), opts.failure_budget)
    return results
