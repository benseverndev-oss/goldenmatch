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
        cfg = dict(ctx.stage_config or {})           # COPY: ctx.stage_config IS StageSpec.config
        apply = cfg.pop("apply_repairs", False)       # pop even when False (never leak to transform_df)

        if apply:
            result = self._run_with_repairs(ctx, cfg)
        elif cfg:
            result = _transform(ctx.df, **cfg)
        else:
            result = _transform(ctx.df)

        if hasattr(result, "df"):
            ctx.df = result.df
        if hasattr(result, "manifest"):
            ctx.artifacts["manifest"] = result.manifest
            if "column_contexts" in ctx.artifacts:
                try:
                    from goldenpipe.models.column_context import enrich_contexts_from_flow
                    enrich_contexts_from_flow(ctx.artifacts["column_contexts"], result.manifest)
                except Exception:
                    logger.exception("Failed to enrich column contexts from flow manifest")

        return StageResult(status=StageStatus.SUCCESS)

    def _run_with_repairs(self, ctx: PipeContext, cfg: dict):
        """apply_repairs is on: merge the repair plan's fixer transforms into the
        base GoldenFlowConfig and run explicit mode. Falls through to the normal
        path when there is nothing to apply (keeps auto-detect / byte-identity)."""
        from goldenpipe.repair_host import merge_transforms, repair_transform_specs

        plan = ctx.artifacts.get("repair_plan")
        specs, skipped = repair_transform_specs(plan) if plan else ([], [])
        base = dict(cfg.get("config") or {})
        user_transforms = list(base.get("transforms") or [])

        if not specs and not user_transforms:
            if skipped:
                ctx.reasoning["repair_skipped"] = "; ".join(f"{s['column']}:{s['op']}" for s in skipped)
            # nothing to inject -> behave exactly like the gate-off path
            return _transform(ctx.df, **cfg) if cfg else _transform(ctx.df)

        base["transforms"] = merge_transforms(user_transforms, specs)
        if skipped:
            ctx.reasoning["repair_skipped"] = "; ".join(f"{s['column']}:{s['op']}" for s in skipped)
        logger.info("Applying %d repair transform spec(s); skipped %d assertion(s)", len(specs), len(skipped))
        return _transform(ctx.df, config=base)
