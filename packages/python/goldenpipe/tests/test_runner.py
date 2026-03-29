"""Tests for pipeline runner."""
from goldenpipe.engine.runner import Runner
from goldenpipe.engine.resolver import PlannedStage, ExecutionPlan
from goldenpipe.engine.registry import StageRegistry
from goldenpipe.models.config import StageSpec
from goldenpipe.models.context import PipeContext, StageResult, StageStatus, Decision
from goldenpipe.models.stage import stage


@stage(name="success_stage", produces=["df"], consumes=[])
def success_stage(ctx: PipeContext) -> StageResult:
    return StageResult(status=StageStatus.SUCCESS)

@stage(name="fail_stage", produces=[], consumes=[])
def fail_stage(ctx: PipeContext) -> StageResult:
    raise RuntimeError("boom")

@stage(name="skip_decider", produces=[], consumes=[])
def skip_decider(ctx: PipeContext) -> StageResult:
    return StageResult(
        status=StageStatus.SUCCESS,
        decision=Decision(skip=["fail_stage"], reason="skip it"),
    )

@stage(name="abort_decider", produces=[], consumes=[])
def abort_decider(ctx: PipeContext) -> StageResult:
    return StageResult(
        status=StageStatus.SUCCESS,
        decision=Decision(abort=True, reason="fatal"),
    )


def _plan(*stages) -> ExecutionPlan:
    planned = []
    for s in stages:
        planned.append(PlannedStage(
            name=s.info.name, stage=s, spec=StageSpec(use=s.info.name),
        ))
    return ExecutionPlan(stages=planned)


class TestRunner:
    def test_run_single_success(self):
        ctx = PipeContext()
        runner = Runner(registry=StageRegistry())
        result = runner.run(_plan(success_stage), ctx)
        assert "success_stage" in result
        assert result["success_stage"].status == StageStatus.SUCCESS

    def test_run_records_timing(self):
        ctx = PipeContext()
        runner = Runner(registry=StageRegistry())
        runner.run(_plan(success_stage), ctx)
        assert "success_stage" in ctx.timing
        assert ctx.timing["success_stage"] >= 0

    def test_run_failure_continues(self):
        ctx = PipeContext()
        runner = Runner(registry=StageRegistry())
        result = runner.run(_plan(fail_stage, success_stage), ctx)
        assert result["fail_stage"].status == StageStatus.FAILED
        assert result["fail_stage"].error == "boom"
        assert result["success_stage"].status == StageStatus.SUCCESS

    def test_run_failure_aborts_on_error(self):
        ctx = PipeContext()
        runner = Runner(registry=StageRegistry())
        plan = _plan(fail_stage, success_stage)
        plan.stages[0].spec = StageSpec(use="fail_stage", on_error="abort")
        result = runner.run(plan, ctx)
        assert result["fail_stage"].status == StageStatus.FAILED
        assert "success_stage" not in result

    def test_decision_skip(self):
        ctx = PipeContext()
        runner = Runner(registry=StageRegistry())
        result = runner.run(_plan(skip_decider, fail_stage), ctx)
        assert "skip_decider" in result
        assert "fail_stage" not in result

    def test_decision_abort(self):
        ctx = PipeContext()
        runner = Runner(registry=StageRegistry())
        result = runner.run(_plan(abort_decider, success_stage), ctx)
        assert "abort_decider" in result
        assert "success_stage" not in result

    def test_skip_if_artifact_missing(self):
        ctx = PipeContext()
        runner = Runner(registry=StageRegistry())
        plan = _plan(success_stage)
        plan.stages[0].spec = StageSpec(use="success_stage", skip_if="findings")
        result = runner.run(plan, ctx)
        assert result["success_stage"].status == StageStatus.SKIPPED

    def test_skip_if_artifact_present(self):
        ctx = PipeContext()
        ctx.artifacts["findings"] = [{"issue": "nulls"}]
        runner = Runner(registry=StageRegistry())
        plan = _plan(success_stage)
        plan.stages[0].spec = StageSpec(use="success_stage", skip_if="findings")
        result = runner.run(plan, ctx)
        assert result["success_stage"].status == StageStatus.SUCCESS
