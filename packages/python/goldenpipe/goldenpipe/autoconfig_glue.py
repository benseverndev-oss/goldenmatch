"""Host glue for the auto-config brain (Polars/InferMap in; Pydantic out).

NOT ported to Rust — the future `goldenpipe-core` kernel is the portable core
(autoconfig_planner). This bracket does the impure extraction + materialization.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

from goldenpipe.autoconfig_planner import (
    ComplexityProfile,
    PipePlan,
    PipeProfile,
    PlannerInput,
    band_of,
)
from goldenpipe.errors import PipeNotConfidentError
from goldenpipe.models.config import PipelineConfig, StageSpec
from goldenpipe.models.context import PipeContext


def profile_context(ctx: PipeContext) -> PipeProfile:
    """Build the portable PipeProfile from a loaded context (cheap, no row scan)."""
    df = ctx.df
    if df is None:
        return PipeProfile(
            n_rows=int(ctx.metadata.get("input_rows", 0)),
            n_cols=0,
            column_names=(),
            dtypes=(),
            inferred_domain=None,
            domain_confidence=0.0,
        )

    column_names = tuple(df.columns)
    dtypes = tuple(str(dt) for dt in df.dtypes)

    from infermap import detect_domain_detailed

    det = detect_domain_detailed(SimpleNamespace(columns=list(column_names)))
    inferred_domain = det.domain
    domain_confidence = det.score if det.domain is not None else 0.0

    return PipeProfile(
        n_rows=len(df),
        n_cols=len(column_names),
        column_names=column_names,
        dtypes=dtypes,
        inferred_domain=inferred_domain,
        domain_confidence=domain_confidence,
    )


logger = logging.getLogger(__name__)

REFUSE_ROW_THRESHOLD = 100_000


def profile_complexity(ctx: PipeContext) -> ComplexityProfile:
    """One columnar pass for null density. Engine-resident or empty -> zeros
    (unknown; not profiled this slice)."""
    df = ctx.df
    if df is None:
        return ComplexityProfile(max_null_density=0.0, mean_null_density=0.0)
    n_rows = len(df)
    if n_rows == 0:
        return ComplexityProfile(max_null_density=0.0, mean_null_density=0.0)
    # null_count() -> a 1-row frame of per-column null counts.
    counts = df.null_count().row(0)
    fractions = [c / n_rows for c in counts]
    return ComplexityProfile(
        max_null_density=max(fractions),
        mean_null_density=sum(fractions) / len(fractions),
    )


def build_planner_input(ctx: PipeContext) -> PlannerInput:
    """Assemble the full decision input (runtime + complexity) from a context."""
    return PlannerInput(
        runtime=profile_context(ctx),
        complexity=profile_complexity(ctx),
    )


def enforce_confidence(plan: PipePlan, runtime: PipeProfile) -> None:
    """Refuse-on-RED (size-gated). Raises PipeNotConfidentError on a red-band
    plan at/above the row threshold; warns and proceeds below it; no-op otherwise."""
    if band_of(plan.confidence) != "red":
        return
    if runtime.n_rows >= REFUSE_ROW_THRESHOLD:
        raise PipeNotConfidentError(
            f"auto-config not confident (rule={plan.rule_name}, "
            f"confidence={plan.confidence}) on {runtime.n_rows} rows; "
            f"supply an explicit pipeline config or reduce the input size. "
            f"evidence={plan.evidence}"
        )
    logger.warning(
        "auto-config low confidence (rule=%s) on %d rows; proceeding on safe "
        "default plan", plan.rule_name, runtime.n_rows,
    )


def plan_to_config(
    plan: PipePlan,
    available: Any,
    identity_opts: dict | None,
) -> PipelineConfig:
    """Materialize a PipePlan into a Pydantic PipelineConfig, filtering by availability."""
    specs: list[StageSpec] = [
        StageSpec(use=s.name, config=dict(s.config))
        for s in plan.stages
        if s.name in available
    ]
    if identity_opts and "goldenmatch.identity_resolve" in available:
        specs.append(StageSpec(use="goldenmatch.identity_resolve", config={**identity_opts}))
    return PipelineConfig(pipeline="auto", stages=specs)
