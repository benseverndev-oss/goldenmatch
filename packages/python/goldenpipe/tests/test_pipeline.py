"""Tests for Pipeline class."""
from goldenpipe.pipeline import Pipeline
from goldenpipe.models.config import PipelineConfig, StageSpec
from goldenpipe.models.context import PipeStatus, StageStatus, PipeContext, StageResult
from goldenpipe.models.stage import stage
from goldenpipe.engine.registry import StageRegistry


@stage(name="noop", produces=["df"], consumes=["df"])
def noop_stage(ctx: PipeContext) -> StageResult:
    return StageResult(status=StageStatus.SUCCESS)


class TestPipeline:
    def test_run_csv(self, sample_csv):
        reg = StageRegistry()
        reg.register(noop_stage)
        config = PipelineConfig(pipeline="test", stages=[StageSpec(use="noop")])
        pipe = Pipeline(config=config, registry=reg)
        result = pipe.run(source=str(sample_csv))
        assert result.status == PipeStatus.SUCCESS
        assert result.input_rows == 5

    def test_run_df(self, sample_df):
        reg = StageRegistry()
        reg.register(noop_stage)
        config = PipelineConfig(pipeline="test", stages=[StageSpec(use="noop")])
        pipe = Pipeline(config=config, registry=reg)
        result = pipe.run(df=sample_df)
        assert result.status == PipeStatus.SUCCESS
        assert result.input_rows == 5

    def test_run_no_source(self):
        pipe = Pipeline()
        result = pipe.run()
        assert result.status == PipeStatus.FAILED

    def test_run_nonexistent_file(self):
        pipe = Pipeline()
        result = pipe.run(source="/nonexistent.csv")
        assert result.status == PipeStatus.FAILED

    def test_auto_config(self):
        pipe = Pipeline()
        config = pipe._auto_config()
        assert config.pipeline == "auto"


class TestImports:
    def test_public_api(self):
        import goldenpipe as gp
        assert hasattr(gp, "run")
        assert hasattr(gp, "run_df")
        assert hasattr(gp, "run_stages")
        assert hasattr(gp, "Pipeline")
        assert hasattr(gp, "PipeResult")
        assert hasattr(gp, "stage")
        assert hasattr(gp, "__version__")
        assert gp.__version__ == "1.0.1"
