import polars as pl
import pytest
from goldenpipe.autoconfig_glue import plan_to_config, profile_context
from goldenpipe.autoconfig_planner import PipePlan, PlannedStage
from goldenpipe.models.config import PipelineConfig
from goldenpipe.models.context import PipeContext


def test_profile_context_materialized_df_detects_finance():
    df = pl.DataFrame({"account_number": ["A1", "A2"], "currency": ["USD", "EUR"]})
    ctx = PipeContext(df=df)
    prof = profile_context(ctx)
    assert prof.n_rows == 2
    assert prof.n_cols == 2
    assert prof.column_names == ("account_number", "currency")
    assert prof.inferred_domain == "finance"
    assert prof.domain_confidence > 0.0


def test_profile_context_no_domain_gives_zero_confidence():
    df = pl.DataFrame({"x": [1, 2], "y": [3, 4]})
    prof = profile_context(PipeContext(df=df))
    if prof.inferred_domain is None:
        assert prof.domain_confidence == 0.0


def test_profile_context_engine_resident_is_degraded():
    ctx = PipeContext(df=None)
    ctx.metadata["input_rows"] = 5000
    prof = profile_context(ctx)
    assert prof.n_rows == 5000
    assert prof.column_names == ()
    assert prof.inferred_domain is None
    assert prof.domain_confidence == 0.0


def test_plan_to_config_filters_by_availability_and_builds_stagespecs():
    plan = PipePlan(
        stages=(
            PlannedStage("infer_schema", {"domain": "finance"}),
            PlannedStage("goldencheck.scan", {}),
            PlannedStage("missing.stage", {}),
        ),
        rule_name="confident_schema", confidence=0.8, evidence={},
    )
    available = {"infer_schema": object(), "goldencheck.scan": object()}
    cfg = plan_to_config(plan, available, identity_opts=None)
    assert isinstance(cfg, PipelineConfig)
    assert cfg.pipeline == "auto"
    uses = [s.use for s in cfg.stages]
    assert uses == ["infer_schema", "goldencheck.scan"]
    assert cfg.stages[0].config == {"domain": "finance"}


def test_plan_to_config_appends_identity_when_opts_and_available():
    plan = PipePlan((PlannedStage("goldencheck.scan", {}),), "default", 0.7, {})
    available = {"goldencheck.scan": object(), "goldenmatch.identity_resolve": object()}
    cfg = plan_to_config(plan, available, identity_opts={"kinds": ["email"]})
    assert [s.use for s in cfg.stages] == ["goldencheck.scan", "goldenmatch.identity_resolve"]
    assert cfg.stages[-1].config == {"kinds": ["email"]}


# --- Integration: _plan_config end-to-end through the brain -------------------

from goldenpipe.engine.registry import StageRegistry  # noqa: E402
from goldenpipe.models.context import PipeStatus, StageResult, StageStatus  # noqa: E402
from goldenpipe.models.stage import stage  # noqa: E402
from goldenpipe.pipeline import Pipeline  # noqa: E402


def _stub_stage(name: str):
    """A minimal always-succeeds Stage registered under a chosen name."""

    @stage(name=name, produces=[], consumes=[])
    def _run(ctx: PipeContext) -> StageResult:
        return StageResult(status=StageStatus.SUCCESS)

    return _run


def _registry_with(*names: str) -> StageRegistry:
    reg = StageRegistry()
    for n in names:
        reg.register(_stub_stage(n))
    return reg


def test_plan_config_confident_df_includes_infer_schema():
    reg = _registry_with(
        "infer_schema",
        "goldencheck.scan",
        "goldenflow.transform",
        "goldenmatch.dedupe",
    )
    eng = Pipeline(registry=reg)
    df = pl.DataFrame({"account_number": ["A1", "A2"], "currency": ["USD", "EUR"]})
    ctx = PipeContext(df=df)
    cfg = eng._plan_config(ctx)
    uses = [s.use for s in cfg.stages]
    assert uses == [
        "infer_schema",
        "goldencheck.scan",
        "goldenflow.transform",
        "goldenmatch.dedupe",
    ]
    assert eng._last_plan.rule_name == "confident_schema"
    assert cfg.stages[0].config == {"domain": "finance"}


def test_plan_config_one_row_is_pathological_and_skips_dedupe():
    reg = _registry_with(
        "infer_schema",
        "goldencheck.scan",
        "goldenflow.transform",
        "goldenmatch.dedupe",
    )
    eng = Pipeline(registry=reg)
    ctx = PipeContext(df=pl.DataFrame({"x": [1]}))
    cfg = eng._plan_config(ctx)
    uses = [s.use for s in cfg.stages]
    assert uses == ["goldencheck.scan", "goldenflow.transform"]
    assert "goldenmatch.dedupe" not in uses
    assert eng._last_plan.rule_name == "pathological"


def test_plan_config_runs_end_to_end_and_preserves_order():
    reg = _registry_with(
        "infer_schema",
        "goldencheck.scan",
        "goldenflow.transform",
        "goldenmatch.dedupe",
    )
    eng = Pipeline(registry=reg)
    df = pl.DataFrame({"account_number": ["A1", "A2"], "currency": ["USD", "EUR"]})
    result = eng.run(df=df)
    assert result.status == PipeStatus.SUCCESS
    assert eng._last_plan.rule_name == "confident_schema"
    ran = list(result.stages)
    assert ran == [
        "infer_schema",
        "goldencheck.scan",
        "goldenflow.transform",
        "goldenmatch.dedupe",
    ]


# --- Slice 2: complexity profiling + refuse ----------------------------------

import dataclasses  # noqa: E402

from goldenpipe.autoconfig_glue import (  # noqa: E402
    build_planner_input,
    enforce_confidence,
    profile_complexity,
)
from goldenpipe.autoconfig_planner import PlannerInput  # noqa: E402
from goldenpipe.errors import PipeNotConfidentError  # noqa: E402


def _replace_n_rows(profile, n):
    return dataclasses.replace(profile, n_rows=n)


def test_profile_complexity_null_heavy_column():
    df = pl.DataFrame({"a": [1, None, None, None], "b": [1, 2, 3, 4]})
    comp = profile_complexity(PipeContext(df=df))
    assert comp.max_null_density == 0.75      # column a: 3/4
    assert comp.mean_null_density == 0.375     # (0.75 + 0.0) / 2


def test_profile_complexity_no_nulls_is_zero():
    df = pl.DataFrame({"a": [1, 2], "b": [3, 4]})
    comp = profile_complexity(PipeContext(df=df))
    assert comp.max_null_density == 0.0
    assert comp.mean_null_density == 0.0


def test_profile_complexity_engine_resident_is_zero():
    comp = profile_complexity(PipeContext(df=None))
    assert comp.max_null_density == 0.0
    assert comp.mean_null_density == 0.0


def test_profile_complexity_empty_df_is_zero():
    df = pl.DataFrame({"a": []})
    comp = profile_complexity(PipeContext(df=df))
    assert comp.max_null_density == 0.0
    assert comp.mean_null_density == 0.0


def test_build_planner_input_bundles_runtime_and_complexity():
    df = pl.DataFrame({"account_number": ["A1", "A2"], "currency": ["USD", "EUR"]})
    inp = build_planner_input(PipeContext(df=df))
    assert isinstance(inp, PlannerInput)
    assert inp.runtime.n_rows == 2
    assert inp.runtime.inferred_domain == "finance"
    assert inp.complexity.max_null_density == 0.0


def _red_plan():
    return PipePlan(stages=(), rule_name="low_confidence", confidence=0.3, evidence={})


def _green_plan():
    return PipePlan(stages=(), rule_name="default", confidence=0.7, evidence={})


def test_enforce_confidence_red_at_scale_raises():
    runtime = profile_context(PipeContext(df=pl.DataFrame({"x": [1, 2]})))
    runtime = _replace_n_rows(runtime, 100_000)
    with pytest.raises(PipeNotConfidentError):
        enforce_confidence(_red_plan(), runtime)


def test_enforce_confidence_red_small_proceeds():
    runtime = profile_context(PipeContext(df=pl.DataFrame({"x": [1, 2]})))
    runtime = _replace_n_rows(runtime, 99_999)
    assert enforce_confidence(_red_plan(), runtime) is None


def test_enforce_confidence_green_proceeds():
    runtime = profile_context(PipeContext(df=pl.DataFrame({"x": [1, 2]})))
    runtime = _replace_n_rows(runtime, 100_000)
    assert enforce_confidence(_green_plan(), runtime) is None
