"""GoldenFlow adapter -- wraps transform_df()."""
from __future__ import annotations

from goldenpipe.models.context import PipeContext, StageResult, StageStatus
from goldenpipe.models.stage import StageInfo

try:
    from goldenflow import transform_df as _transform
    HAS_FLOW = True
except ImportError:
    HAS_FLOW = False
    _transform = None


class TransformStage:
    info = StageInfo(name="goldenflow.transform", produces=["df", "manifest"], consumes=["df"])
    rollback = None

    def validate(self, ctx: PipeContext) -> None:
        if not HAS_FLOW:
            raise RuntimeError("GoldenFlow not installed. Run: pip install goldenpipe[flow]")

    def run(self, ctx: PipeContext) -> StageResult:
        result = _transform(ctx.df)
        if hasattr(result, "df"):
            ctx.df = result.df
        if hasattr(result, "manifest"):
            ctx.artifacts["manifest"] = result.manifest
        return StageResult(status=StageStatus.SUCCESS)
