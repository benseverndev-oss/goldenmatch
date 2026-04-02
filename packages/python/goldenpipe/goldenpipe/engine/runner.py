"""Pipeline runner -- execute stages with error handling and routing."""
from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

from goldenpipe.engine.registry import StageRegistry
from goldenpipe.engine.resolver import ExecutionPlan
from goldenpipe.engine.router import Router
from goldenpipe.models.context import PipeContext, StageResult, StageStatus


class Runner:
    """Executes an ExecutionPlan against a PipeContext."""

    def __init__(self, registry: StageRegistry) -> None:
        self._registry = registry

    def run(self, plan: ExecutionPlan, ctx: PipeContext) -> dict[str, StageResult]:
        results: dict[str, StageResult] = {}
        remaining = list(plan.stages)

        while remaining:
            planned = remaining.pop(0)

            if planned.spec.skip_if:
                artifact = ctx.artifacts.get(planned.spec.skip_if)
                if not artifact:
                    result = StageResult(status=StageStatus.SKIPPED)
                    results[planned.name] = result
                    ctx.reasoning[planned.name] = (
                        f"Skipped: artifact '{planned.spec.skip_if}' is missing/falsy"
                    )
                    continue

            start = time.perf_counter()
            try:
                # Make stage-level config available to the adapter via context
                ctx.stage_config = planned.config

                if hasattr(planned.stage, "validate") and callable(planned.stage.validate):
                    planned.stage.validate(ctx)
                result = planned.stage.run(ctx)
                elapsed = time.perf_counter() - start
                ctx.timing[planned.name] = elapsed
                results[planned.name] = result

                if result.decision is not None:
                    remaining = Router.apply(
                        result.decision, remaining, ctx, self._registry,
                    )

            except Exception as e:
                import traceback as _tb
                logger.error("Stage %s failed:\n%s", planned.name, _tb.format_exc())
                elapsed = time.perf_counter() - start
                ctx.timing[planned.name] = elapsed
                result = StageResult(status=StageStatus.FAILED, error=str(e))
                results[planned.name] = result
                ctx.reasoning[planned.name] = f"Failed: {e}"

                if planned.spec.on_error == "abort":
                    break

        return results
