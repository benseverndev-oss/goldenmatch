"""Tests for Stage protocol and @stage decorator."""
import polars as pl
from goldenpipe.models.stage import StageInfo, stage
from goldenpipe.models.context import PipeContext, StageResult, StageStatus


class TestStageInfo:
    def test_create(self):
        info = StageInfo(name="test", produces=["df"], consumes=["df"])
        assert info.name == "test"
        assert info.produces == ["df"]
        assert info.consumes == ["df"]
        assert info.config_schema is None

    def test_with_config_schema(self):
        info = StageInfo(name="test", produces=[], consumes=[], config_schema=dict)
        assert info.config_schema is dict


class TestStageDecorator:
    def test_basic_decoration(self):
        @stage(name="my_stage", produces=["df"], consumes=["df"])
        def my_stage(ctx: PipeContext) -> StageResult:
            return StageResult(status=StageStatus.SUCCESS)

        assert my_stage.info.name == "my_stage"
        assert my_stage.info.produces == ["df"]
        assert my_stage.info.consumes == ["df"]

    def test_run(self, sample_df):
        @stage(name="noop", produces=["df"], consumes=["df"])
        def noop(ctx: PipeContext) -> StageResult:
            return StageResult(status=StageStatus.SUCCESS)

        ctx = PipeContext(df=sample_df)
        result = noop.run(ctx)
        assert result.status == StageStatus.SUCCESS

    def test_validate_exists(self):
        @stage(name="test", produces=[], consumes=[])
        def test_fn(ctx: PipeContext) -> StageResult:
            return StageResult(status=StageStatus.SUCCESS)

        ctx = PipeContext()
        test_fn.validate(ctx)  # should not raise

    def test_no_rollback_by_default(self):
        @stage(name="test", produces=[], consumes=[])
        def test_fn(ctx: PipeContext) -> StageResult:
            return StageResult(status=StageStatus.SUCCESS)

        assert not hasattr(test_fn, "rollback") or test_fn.rollback is None

    def test_mutates_context(self, sample_df):
        @stage(name="upper", produces=["df"], consumes=["df"])
        def upper_names(ctx: PipeContext) -> StageResult:
            ctx.df = ctx.df.with_columns(pl.col("name").str.to_uppercase())
            return StageResult(status=StageStatus.SUCCESS)

        ctx = PipeContext(df=sample_df)
        upper_names.run(ctx)
        assert ctx.df["name"][0] == "JOHN SMITH"

    def test_produces_artifact(self):
        @stage(name="producer", produces=["findings"], consumes=[])
        def produce(ctx: PipeContext) -> StageResult:
            ctx.artifacts["findings"] = [{"col": "name", "issue": "nulls"}]
            return StageResult(status=StageStatus.SUCCESS)

        ctx = PipeContext()
        produce.run(ctx)
        assert "findings" in ctx.artifacts

    def test_returns_decision(self):
        from goldenpipe.models.context import Decision

        @stage(name="gate", produces=[], consumes=[])
        def gate(ctx: PipeContext) -> StageResult:
            return StageResult(
                status=StageStatus.SUCCESS,
                decision=Decision(abort=True, reason="stop"),
            )

        ctx = PipeContext()
        result = gate.run(ctx)
        assert result.decision is not None
        assert result.decision.abort is True
