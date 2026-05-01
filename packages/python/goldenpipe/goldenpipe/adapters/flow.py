"""GoldenFlow adapter -- wraps transform_df()."""
from __future__ import annotations

import logging

from goldenpipe.models.context import PipeContext, StageResult, StageStatus
from goldenpipe.models.stage import StageInfo

logger = logging.getLogger(__name__)

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
        stage_cfg = ctx.stage_config
        if stage_cfg:
            logger.info("Passing stage config to GoldenFlow transform")
            result = _transform(ctx.df, **stage_cfg)
        else:
            result = _transform(ctx.df)
        if hasattr(result, "df"):
            ctx.df = result.df
        if hasattr(result, "manifest"):
            ctx.artifacts["manifest"] = result.manifest

            # Enrich column contexts with transform information (best-effort)
            if "column_contexts" in ctx.artifacts:
                try:
                    from goldenpipe.models.column_context import enrich_contexts_from_flow
                    enrich_contexts_from_flow(ctx.artifacts["column_contexts"], result.manifest)
                except Exception:
                    logger.exception("Failed to enrich column contexts from flow manifest")

        return StageResult(status=StageStatus.SUCCESS)
