"""Tests for PipeResult construction."""
from goldenpipe.engine.reporter import Reporter
from goldenpipe.models.context import (
    PipeContext, StageResult, StageStatus, PipeStatus,
)


class TestReporter:
    def test_all_success(self):
        ctx = PipeContext(metadata={"source": "test.csv", "input_rows": 5})
        ctx.timing = {"a": 1.0, "b": 2.0}
        ctx.reasoning = {"a": "ran"}
        stages = {
            "a": StageResult(status=StageStatus.SUCCESS),
            "b": StageResult(status=StageStatus.SUCCESS),
        }
        result = Reporter.build(ctx, stages)
        assert result.status == PipeStatus.SUCCESS
        assert result.source == "test.csv"
        assert result.input_rows == 5

    def test_all_failed(self):
        ctx = PipeContext(metadata={"source": "test.csv", "input_rows": 0})
        stages = {"a": StageResult(status=StageStatus.FAILED, error="err")}
        result = Reporter.build(ctx, stages)
        assert result.status == PipeStatus.FAILED
        assert result.errors == ["a: err"]

    def test_partial(self):
        ctx = PipeContext(metadata={"source": "test.csv", "input_rows": 5})
        stages = {
            "a": StageResult(status=StageStatus.SUCCESS),
            "b": StageResult(status=StageStatus.FAILED, error="boom"),
        }
        result = Reporter.build(ctx, stages)
        assert result.status == PipeStatus.PARTIAL
        assert "b: boom" in result.errors

    def test_skipped_collected(self):
        ctx = PipeContext(metadata={"source": "test.csv", "input_rows": 5})
        stages = {
            "a": StageResult(status=StageStatus.SUCCESS),
            "b": StageResult(status=StageStatus.SKIPPED),
        }
        result = Reporter.build(ctx, stages)
        assert result.status == PipeStatus.SUCCESS
        assert "b" in result.skipped

    def test_all_skipped_is_success(self):
        ctx = PipeContext(metadata={"source": "test.csv", "input_rows": 5})
        stages = {
            "a": StageResult(status=StageStatus.SKIPPED),
            "b": StageResult(status=StageStatus.SKIPPED),
        }
        result = Reporter.build(ctx, stages)
        assert result.status == PipeStatus.SUCCESS

    def test_empty_stages_is_success(self):
        ctx = PipeContext(metadata={"source": "test.csv", "input_rows": 5})
        result = Reporter.build(ctx, {})
        assert result.status == PipeStatus.SUCCESS

    def test_artifacts_copied(self):
        ctx = PipeContext(metadata={"source": "test.csv", "input_rows": 5})
        ctx.artifacts["findings"] = [1, 2, 3]
        stages = {"a": StageResult(status=StageStatus.SUCCESS)}
        result = Reporter.build(ctx, stages)
        assert result.artifacts["findings"] == [1, 2, 3]

    def test_timing_and_reasoning(self):
        ctx = PipeContext(metadata={"source": "test.csv", "input_rows": 5})
        ctx.timing = {"a": 1.5}
        ctx.reasoning = {"a": "auto-detected"}
        stages = {"a": StageResult(status=StageStatus.SUCCESS)}
        result = Reporter.build(ctx, stages)
        assert result.timing["a"] == 1.5
        assert result.reasoning["a"] == "auto-detected"
