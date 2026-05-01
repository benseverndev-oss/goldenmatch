"""Pipeline -- thin wrapper over the engine layer."""
from __future__ import annotations


import polars as pl

from goldenpipe.engine.registry import StageRegistry
from goldenpipe.engine.resolver import Resolver
from goldenpipe.engine.runner import Runner
from goldenpipe.engine.reporter import Reporter
from goldenpipe.models.config import PipelineConfig, StageSpec
from goldenpipe.models.context import PipeContext, PipeResult, PipeStatus


class Pipeline:
    """High-level pipeline orchestrator."""

    def __init__(
        self,
        config: PipelineConfig | None = None,
        registry: StageRegistry | None = None,
    ) -> None:
        self._config = config
        self._registry = registry or StageRegistry()
        if registry is None:
            self._registry.discover()

    def run(self, source: str | None = None, df: pl.DataFrame | None = None) -> PipeResult:
        ctx = PipeContext()

        if df is not None:
            ctx.df = df
            ctx.metadata["source"] = "<DataFrame>"
            ctx.metadata["input_rows"] = len(df)
        elif source:
            try:
                ctx.df = pl.read_csv(source, ignore_errors=True, encoding="utf8-lossy")
                ctx.metadata["source"] = source
                ctx.metadata["input_rows"] = len(ctx.df)
            except Exception as e:
                return PipeResult(
                    status=PipeStatus.FAILED,
                    source=source or "",
                    input_rows=0,
                    errors=[f"Failed to load data: {e}"],
                )
        else:
            return PipeResult(
                status=PipeStatus.FAILED,
                source="",
                input_rows=0,
                errors=["No source file or DataFrame provided"],
            )

        config = self._config or self._auto_config()

        try:
            plan = Resolver.resolve(config, self._registry)
        except Exception as e:
            return PipeResult(
                status=PipeStatus.FAILED,
                source=ctx.metadata.get("source", ""),
                input_rows=ctx.metadata.get("input_rows", 0),
                errors=[f"Pipeline resolution failed: {e}"],
            )

        runner = Runner(registry=self._registry)
        stages = runner.run(plan, ctx)
        return Reporter.build(ctx, stages)

    def _auto_config(self) -> PipelineConfig:
        available = self._registry.list_all()
        stage_specs: list[StageSpec | str] = []
        for name in ["goldencheck.scan", "goldenflow.transform", "goldenmatch.dedupe"]:
            if name in available:
                stage_specs.append(StageSpec(use=name))
        return PipelineConfig(pipeline="auto", stages=stage_specs)
