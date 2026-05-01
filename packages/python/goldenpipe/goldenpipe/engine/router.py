"""Decision router -- apply routing decisions to execution plan."""
from __future__ import annotations

from goldenpipe.engine.registry import StageRegistry
from goldenpipe.engine.resolver import PlannedStage
from goldenpipe.models.config import StageSpec
from goldenpipe.models.context import Decision, PipeContext


class Router:
    """Applies Decision objects to modify the remaining execution plan."""

    @staticmethod
    def apply(
        decision: Decision,
        remaining: list[PlannedStage],
        ctx: PipeContext,
        registry: StageRegistry,
    ) -> list[PlannedStage]:
        if decision.reason:
            ctx.reasoning["_router"] = decision.reason

        if decision.abort:
            ctx.reasoning["_router"] = f"ABORT: {decision.reason}"
            return []

        if decision.skip:
            remaining = [s for s in remaining if s.name not in decision.skip]

        if decision.insert:
            inserted = []
            for name in reversed(decision.insert):
                stage_obj = registry.get(name)
                inserted.insert(0, PlannedStage(
                    name=name,
                    stage=stage_obj,
                    spec=StageSpec(use=name),
                ))
            remaining = inserted + remaining

        return remaining
