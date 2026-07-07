"""Pipeline -- thin wrapper over the engine layer."""
from __future__ import annotations

import re

import polars as pl

from goldenpipe.engine.registry import StageRegistry
from goldenpipe.engine.reporter import Reporter
from goldenpipe.engine.resolver import Resolver
from goldenpipe.engine.runner import Runner
from goldenpipe.models.config import PipelineConfig, StageSpec
from goldenpipe.models.context import PipeContext, PipeResult, PipeStatus

_TABLE_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_.]*$")


class Pipeline:
    """High-level pipeline orchestrator."""

    def __init__(
        self,
        config: PipelineConfig | None = None,
        registry: StageRegistry | None = None,
        identity_opts: dict | None = None,
    ) -> None:
        self._config = config
        self._registry = registry or StageRegistry()
        if registry is None:
            self._registry.discover()
        # v1.2: identity_opts is only meaningful in the auto-config path.
        # When the caller supplied an explicit PipelineConfig (YAML), the
        # YAML is authoritative and identity_opts is ignored.
        self._identity_opts = identity_opts
        # Set by _plan_config on each run; exposes the last plan-first
        # auto-config decision (rule_name, confidence, evidence) for
        # introspection. None until the brain has planned at least once.
        self._last_plan = None

    def run(
        self,
        source: str | None = None,
        df: pl.DataFrame | None = None,
        *,
        duckdb_con=None,
        duckdb_table: str | None = None,
    ) -> PipeResult:
        ctx = PipeContext()

        if duckdb_con is not None and duckdb_table is not None:
            # Engine-resident source (relocatable-stage contract, Phase C v2):
            # the data ORIGINATES in DuckDB, so ``ctx.frame`` starts as a lazy
            # ``DuckDBFrame`` and a remote stage right after pays NO ingress
            # crossing. It materializes to ``df`` only at the first LOCAL stage
            # (the Runner does that once), or never for an all-remote pipeline.
            from goldenpipe.models.frame import DuckDBFrame

            if not _TABLE_NAME_RE.match(duckdb_table):
                return PipeResult(
                    status=PipeStatus.FAILED,
                    source=f"duckdb:{duckdb_table}",
                    input_rows=0,
                    errors=[f"Invalid DuckDB table name: {duckdb_table!r}"],
                )
            try:
                rel = duckdb_con.sql(f"SELECT * FROM {duckdb_table}")  # noqa: S608 - name validated above
                ctx.frame = DuckDBFrame(rel)  # engine-resident, NOT materialized
                ctx.metadata["duckdb_con"] = duckdb_con
                ctx.metadata["source"] = f"duckdb:{duckdb_table}"
                # Row count via a scalar COUNT -- a cheap engine aggregate, NOT
                # the full DataFrame pull Phase C confines to egress.
                ctx.metadata["input_rows"] = duckdb_con.sql(
                    f"SELECT count(*) FROM {duckdb_table}"  # noqa: S608 - name validated above
                ).fetchone()[0]
            except Exception as e:
                return PipeResult(
                    status=PipeStatus.FAILED,
                    source=f"duckdb:{duckdb_table}",
                    input_rows=0,
                    errors=[f"Failed to load DuckDB table: {e}"],
                )
        elif df is not None:
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

        config = self._config or self._plan_config(ctx)

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

    def _plan_config(self, ctx: PipeContext) -> PipelineConfig:
        """Plan-first auto-config: profile the loaded context, run the rule
        table, and materialize the chosen plan into a PipelineConfig.

        This is the "brain" (parity with GoldenMatch's controller): the shape
        of the pipeline is DECIDED from the data + InferMap-inferred schema
        rather than being the fixed scan/transform/dedupe list. The portable
        decision core (``autoconfig_planner``) is kept free of Polars/Pydantic
        for the later ``goldenpipe-core`` Rust port; this method is the host
        glue bracket.
        """
        from goldenpipe.autoconfig_glue import plan_to_config, profile_context
        from goldenpipe.autoconfig_planner import plan_pipeline

        profile = profile_context(ctx)
        plan = plan_pipeline(profile)
        self._last_plan = plan
        return plan_to_config(
            plan,
            self._registry.list_all(),
            self._identity_opts,
        )

    def _auto_config(self) -> PipelineConfig:
        available = self._registry.list_all()
        stage_specs: list[StageSpec | str] = []
        for name in ["goldencheck.scan", "goldenflow.transform", "goldenmatch.dedupe"]:
            if name in available:
                stage_specs.append(StageSpec(use=name))
        # v1.2: when identity_opts is supplied and the stage is discoverable,
        # auto-append `goldenmatch.identity_resolve` after dedupe with the
        # opts as its stage_config. Otherwise stay backwards-compatible.
        if self._identity_opts and "goldenmatch.identity_resolve" in available:
            stage_specs.append(StageSpec(
                use="goldenmatch.identity_resolve",
                config={**self._identity_opts},
            ))
        return PipelineConfig(pipeline="auto", stages=stage_specs)
