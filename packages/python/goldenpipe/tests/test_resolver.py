"""Tests for pipeline resolver and wiring validation."""
import pytest
from goldenpipe.engine.resolver import Resolver, ExecutionPlan, PlannedStage, WiringError
from goldenpipe.engine.registry import StageRegistry
from goldenpipe.models.config import PipelineConfig, StageSpec
from goldenpipe.models.stage import stage
from goldenpipe.models.context import PipeContext, StageResult, StageStatus


@stage(name="load", produces=["df"], consumes=[])
def load_stage(ctx: PipeContext) -> StageResult:
    return StageResult(status=StageStatus.SUCCESS)


@stage(name="check", produces=["findings"], consumes=["df"])
def check_stage(ctx: PipeContext) -> StageResult:
    return StageResult(status=StageStatus.SUCCESS)


@stage(name="transform", produces=["df", "manifest"], consumes=["df"])
def transform_stage(ctx: PipeContext) -> StageResult:
    return StageResult(status=StageStatus.SUCCESS)


@stage(name="dedupe", produces=["clusters", "golden"], consumes=["df"])
def dedupe_stage(ctx: PipeContext) -> StageResult:
    return StageResult(status=StageStatus.SUCCESS)


@pytest.fixture
def registry():
    reg = StageRegistry()
    reg.register(load_stage)
    reg.register(check_stage)
    reg.register(transform_stage)
    reg.register(dedupe_stage)
    return reg


class TestResolver:
    def test_resolve_minimal(self, registry):
        config = PipelineConfig(
            pipeline="test",
            stages=[
                StageSpec(use="check"),
                StageSpec(use="transform"),
                StageSpec(use="dedupe"),
            ],
        )
        plan = Resolver.resolve(config, registry)
        assert isinstance(plan, ExecutionPlan)
        assert len(plan.stages) == 4
        assert plan.stages[0].name == "load"

    def test_resolve_bare_strings(self, registry):
        config = PipelineConfig(
            pipeline="test",
            stages=[StageSpec(use="check"), StageSpec(use="dedupe")],
        )
        plan = Resolver.resolve(config, registry)
        names = [s.name for s in plan.stages]
        assert "check" in names
        assert "dedupe" in names

    def test_stage_aliased_name(self, registry):
        config = PipelineConfig(
            pipeline="test",
            stages=[StageSpec(name="validate", use="check")],
        )
        plan = Resolver.resolve(config, registry)
        assert plan.stages[1].name == "validate"

    def test_wiring_error_missing_dependency(self, registry):
        @stage(name="needs_clusters", produces=[], consumes=["clusters"])
        def bad_stage(ctx: PipeContext) -> StageResult:
            return StageResult(status=StageStatus.SUCCESS)
        registry.register(bad_stage)

        config = PipelineConfig(
            pipeline="test",
            stages=[StageSpec(use="needs_clusters")],
        )
        with pytest.raises(WiringError, match="clusters"):
            Resolver.resolve(config, registry)

    def test_wiring_valid_chain(self, registry):
        config = PipelineConfig(
            pipeline="test",
            stages=[
                StageSpec(use="check"),
                StageSpec(use="transform"),
                StageSpec(use="dedupe"),
            ],
        )
        plan = Resolver.resolve(config, registry)
        assert len(plan.stages) == 4

    def test_unknown_stage_raises(self, registry):
        config = PipelineConfig(
            pipeline="test",
            stages=[StageSpec(use="nonexistent")],
        )
        with pytest.raises(KeyError, match="not found"):
            Resolver.resolve(config, registry)

    def test_planned_stage_has_config(self, registry):
        config = PipelineConfig(
            pipeline="test",
            stages=[StageSpec(use="check", config={"threshold": 0.5})],
        )
        plan = Resolver.resolve(config, registry)
        assert plan.stages[1].config == {"threshold": 0.5}
