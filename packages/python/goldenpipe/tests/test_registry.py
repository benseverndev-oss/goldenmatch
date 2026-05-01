"""Tests for stage registry."""
import pytest
from goldenpipe.engine.registry import StageRegistry
from goldenpipe.models.stage import stage
from goldenpipe.models.context import PipeContext, StageResult, StageStatus


@stage(name="dummy", produces=["df"], consumes=["df"])
def dummy_stage(ctx: PipeContext) -> StageResult:
    return StageResult(status=StageStatus.SUCCESS)


class TestStageRegistry:
    def test_register_and_get(self):
        reg = StageRegistry()
        reg.register(dummy_stage)
        assert reg.get("dummy") is dummy_stage

    def test_get_missing_raises(self):
        reg = StageRegistry()
        with pytest.raises(KeyError, match="not found"):
            reg.get("nonexistent")

    def test_list_all(self):
        reg = StageRegistry()
        reg.register(dummy_stage)
        all_stages = reg.list_all()
        assert "dummy" in all_stages
        assert all_stages["dummy"].name == "dummy"

    def test_register_duplicate_overwrites(self):
        reg = StageRegistry()
        reg.register(dummy_stage)

        @stage(name="dummy", produces=[], consumes=[])
        def dummy_v2(ctx: PipeContext) -> StageResult:
            return StageResult(status=StageStatus.SUCCESS)

        reg.register(dummy_v2)
        assert reg.get("dummy") is dummy_v2

    def test_discover_entry_points(self):
        reg = StageRegistry()
        reg.discover()
        all_stages = reg.list_all()
        assert isinstance(all_stages, dict)
        # LoadStage should always be registered
        assert "load" in all_stages

    def test_discover_local_stages_dir(self, tmp_path):
        stages_dir = tmp_path / "stages"
        stages_dir.mkdir()
        (stages_dir / "my_stage.py").write_text(
            "from goldenpipe.models.stage import stage\n"
            "from goldenpipe.models.context import PipeContext, StageResult, StageStatus\n"
            "\n"
            "@stage(name='my_local', produces=['df'], consumes=['df'])\n"
            "def my_local(ctx: PipeContext) -> StageResult:\n"
            "    return StageResult(status=StageStatus.SUCCESS)\n"
        )
        reg = StageRegistry()
        reg.discover(stages_dir=stages_dir)
        assert reg.get("my_local") is not None
