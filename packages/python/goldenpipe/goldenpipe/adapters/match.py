"""GoldenMatch adapter -- wraps dedupe_df()."""
from __future__ import annotations

from goldenpipe.models.context import PipeContext, StageResult, StageStatus
from goldenpipe.models.stage import StageInfo

try:
    from goldenmatch import dedupe_df as _dedupe
    HAS_MATCH = True
except ImportError:
    HAS_MATCH = False
    _dedupe = None


class DedupeStage:
    info = StageInfo(name="goldenmatch.dedupe", produces=["clusters", "golden"], consumes=["df"])
    rollback = None

    def validate(self, ctx: PipeContext) -> None:
        if not HAS_MATCH:
            raise RuntimeError("GoldenMatch not installed. Run: pip install goldenpipe[match]")

    def run(self, ctx: PipeContext) -> StageResult:
        result = _dedupe(ctx.df)
        if hasattr(result, "clusters"):
            ctx.artifacts["clusters"] = result.clusters
        if hasattr(result, "golden"):
            ctx.artifacts["golden"] = result.golden
        return StageResult(status=StageStatus.SUCCESS)
