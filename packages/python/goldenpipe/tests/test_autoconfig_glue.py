import polars as pl
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
