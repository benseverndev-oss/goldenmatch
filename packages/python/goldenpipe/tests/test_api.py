"""Tests for Python API."""
import polars as pl
from goldenpipe._api import run, run_df, run_stages
from goldenpipe.models.context import PipeContext, StageResult, StageStatus, PipeStatus
from goldenpipe.models.stage import stage


@stage(name="upper", produces=["df"], consumes=["df"])
def upper_stage(ctx: PipeContext) -> StageResult:
    ctx.df = ctx.df.with_columns(pl.col("name").str.to_uppercase())
    return StageResult(status=StageStatus.SUCCESS)


class TestRun:
    def test_run_csv(self, sample_csv):
        result = run(str(sample_csv))
        assert result.source == str(sample_csv)
        assert result.input_rows == 5
        assert result.status in (PipeStatus.SUCCESS, PipeStatus.PARTIAL, PipeStatus.FAILED)

    def test_run_nonexistent(self):
        result = run("/nonexistent.csv")
        assert result.status == PipeStatus.FAILED


class TestRunDf:
    def test_run_df(self, sample_df):
        result = run_df(sample_df)
        assert result.input_rows == 5

    def test_run_df_empty(self):
        df = pl.DataFrame({"a": []})
        result = run_df(df)
        assert result.input_rows == 0


class TestRunStages:
    def test_custom_stages(self, sample_df):
        result = run_stages([upper_stage], sample_df)
        assert result.status == PipeStatus.SUCCESS
        assert "upper" in result.stages

    def test_empty_stages(self, sample_df):
        result = run_stages([], sample_df)
        assert result.status == PipeStatus.SUCCESS
