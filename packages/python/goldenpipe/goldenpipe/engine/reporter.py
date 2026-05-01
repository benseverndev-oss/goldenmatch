"""Reporter -- builds PipeResult from PipeContext after execution."""
from __future__ import annotations

from goldenpipe.models.context import (
    PipeContext, PipeResult, PipeStatus, StageResult, StageStatus,
)


class Reporter:
    """Constructs a PipeResult from execution context."""

    @staticmethod
    def build(ctx: PipeContext, stages: dict[str, StageResult]) -> PipeResult:
        errors = [
            f"{name}: {r.error}" for name, r in stages.items()
            if r.status == StageStatus.FAILED and r.error
        ]
        skipped = [
            name for name, r in stages.items()
            if r.status == StageStatus.SKIPPED
        ]

        statuses = [r.status for r in stages.values()]
        non_skip = [s for s in statuses if s != StageStatus.SKIPPED]

        if not non_skip:
            status = PipeStatus.SUCCESS
        elif all(s == StageStatus.FAILED for s in non_skip):
            status = PipeStatus.FAILED
        elif all(s == StageStatus.SUCCESS for s in non_skip):
            status = PipeStatus.SUCCESS
        else:
            status = PipeStatus.PARTIAL

        return PipeResult(
            status=status,
            source=ctx.metadata.get("source", ""),
            input_rows=ctx.metadata.get("input_rows", 0),
            stages=stages,
            artifacts=dict(ctx.artifacts),
            skipped=skipped,
            errors=errors,
            reasoning=dict(ctx.reasoning),
            timing=dict(ctx.timing),
        )
