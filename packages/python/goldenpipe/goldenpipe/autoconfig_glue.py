"""Host glue for the auto-config brain (Polars/InferMap in; Pydantic out).

NOT ported to Rust — the future `goldenpipe-core` kernel is the portable core
(autoconfig_planner). This bracket does the impure extraction + materialization.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from goldenpipe.autoconfig_planner import PipePlan, PipeProfile
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
