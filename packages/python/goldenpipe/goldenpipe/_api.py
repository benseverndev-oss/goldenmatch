"""Python API -- convenience functions for running pipelines."""
from __future__ import annotations

from typing import Any

import polars as pl

from goldenpipe.config.loader import load_config
from goldenpipe.engine.registry import StageRegistry
from goldenpipe.engine.resolver import Resolver
from goldenpipe.engine.runner import Runner
from goldenpipe.engine.reporter import Reporter
from goldenpipe.models.config import PipelineConfig, StageSpec
from goldenpipe.models.context import PipeContext, PipeResult


def run(source: str, config: str | None = None) -> PipeResult:
    """Run a pipeline on a file. Zero-config or from YAML."""
    from goldenpipe.pipeline import Pipeline

    pipeline_config = load_config(config) if config else None
    pipe = Pipeline(config=pipeline_config)
    return pipe.run(source=source)


def run_df(
    df: pl.DataFrame,
    config: str | PipelineConfig | None = None,
) -> PipeResult:
    """Run a pipeline on a DataFrame."""
    from goldenpipe.pipeline import Pipeline

    if isinstance(config, str):
        pipeline_config = load_config(config)
    else:
        pipeline_config = config
    pipe = Pipeline(config=pipeline_config)
    return pipe.run(df=df)


def run_stages(
    stages: list[Any],
    df: pl.DataFrame,
) -> PipeResult:
    """Run specific stages programmatically."""
    registry = StageRegistry()
    for s in stages:
        registry.register(s)

    stage_specs = [StageSpec(use=s.info.name) for s in stages]
    config = PipelineConfig(pipeline="programmatic", stages=stage_specs)

    ctx = PipeContext(
        df=df,
        metadata={"source": "<programmatic>", "input_rows": len(df)},
    )

    plan = Resolver.resolve(config, registry)
    # Remove auto-prepended load stage (we already have df)
    plan.stages = [s for s in plan.stages if s.name != "load"]

    runner = Runner(registry=registry)
    results = runner.run(plan, ctx)
    return Reporter.build(ctx, results)
