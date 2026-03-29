"""Tests for decision routing."""
from goldenpipe.engine.router import Router
from goldenpipe.engine.resolver import PlannedStage
from goldenpipe.engine.registry import StageRegistry
from goldenpipe.models.config import StageSpec
from goldenpipe.models.context import Decision, PipeContext, StageResult, StageStatus
from goldenpipe.models.stage import stage


@stage(name="a", produces=["df"], consumes=[])
def stage_a(ctx: PipeContext) -> StageResult:
    return StageResult(status=StageStatus.SUCCESS)

@stage(name="b", produces=["findings"], consumes=["df"])
def stage_b(ctx: PipeContext) -> StageResult:
    return StageResult(status=StageStatus.SUCCESS)

@stage(name="c", produces=["clusters"], consumes=["df"])
def stage_c(ctx: PipeContext) -> StageResult:
    return StageResult(status=StageStatus.SUCCESS)

@stage(name="c_alt", produces=["clusters"], consumes=["df"])
def stage_c_alt(ctx: PipeContext) -> StageResult:
    return StageResult(status=StageStatus.SUCCESS)


def _make_planned(stg) -> PlannedStage:
    return PlannedStage(name=stg.info.name, stage=stg, spec=StageSpec(use=stg.info.name))


class TestRouter:
    def test_skip_stages(self):
        remaining = [_make_planned(stage_b), _make_planned(stage_c)]
        ctx = PipeContext()
        decision = Decision(skip=["c"], reason="not needed")
        result = Router.apply(decision, remaining, ctx, StageRegistry())
        names = [s.name for s in result]
        assert "c" not in names
        assert "b" in names
        assert "not needed" in ctx.reasoning.values()

    def test_abort_clears_plan(self):
        remaining = [_make_planned(stage_b), _make_planned(stage_c)]
        ctx = PipeContext()
        decision = Decision(abort=True, reason="fatal")
        result = Router.apply(decision, remaining, ctx, StageRegistry())
        assert result == []

    def test_insert_stages(self):
        remaining = [_make_planned(stage_c)]
        ctx = PipeContext()
        reg = StageRegistry()
        reg.register(stage_c_alt)
        decision = Decision(insert=["c_alt"], reason="adding alt")
        result = Router.apply(decision, remaining, ctx, reg)
        names = [s.name for s in result]
        assert names[0] == "c_alt"
        assert names[1] == "c"

    def test_skip_and_insert_replacement(self):
        remaining = [_make_planned(stage_c)]
        ctx = PipeContext()
        reg = StageRegistry()
        reg.register(stage_c_alt)
        decision = Decision(skip=["c"], insert=["c_alt"], reason="swap")
        result = Router.apply(decision, remaining, ctx, reg)
        names = [s.name for s in result]
        assert names == ["c_alt"]

    def test_empty_decision_no_change(self):
        remaining = [_make_planned(stage_b), _make_planned(stage_c)]
        ctx = PipeContext()
        decision = Decision()
        result = Router.apply(decision, remaining, ctx, StageRegistry())
        assert len(result) == 2

    def test_skip_nonexistent_stage_is_noop(self):
        remaining = [_make_planned(stage_b)]
        ctx = PipeContext()
        decision = Decision(skip=["nonexistent"])
        result = Router.apply(decision, remaining, ctx, StageRegistry())
        assert len(result) == 1
