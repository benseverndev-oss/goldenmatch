"""Golden Suite adapters with lazy imports + built-in LoadStage."""
from goldenpipe.models.context import PipeContext, StageResult, StageStatus
from goldenpipe.models.stage import StageInfo


class LoadStage:
    """Built-in stage that marks df as available. Data loading is handled by Pipeline."""

    info = StageInfo(name="load", produces=["df"], consumes=[])
    rollback = None

    def validate(self, ctx: PipeContext) -> None:
        pass

    def run(self, ctx: PipeContext) -> StageResult:
        return StageResult(status=StageStatus.SUCCESS)
