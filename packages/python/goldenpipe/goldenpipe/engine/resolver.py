"""Pipeline resolver -- build ExecutionPlan, validate wiring."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from goldenpipe.engine.registry import StageRegistry
from goldenpipe.models.config import PipelineConfig, StageSpec


class WiringError(Exception):
    """Raised when a stage's consumes can't be satisfied."""


@dataclass
class PlannedStage:
    """A resolved stage ready for execution."""
    name: str
    stage: Any
    spec: StageSpec
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionPlan:
    """Ordered list of stages to execute."""
    stages: list[PlannedStage] = field(default_factory=list)


class Resolver:
    """Builds and validates an ExecutionPlan from config + registry."""

    @staticmethod
    def resolve(config: PipelineConfig, registry: StageRegistry) -> ExecutionPlan:
        plan = ExecutionPlan()
        available_artifacts: set[str] = set()

        # Auto-prepend load stage if available
        try:
            load = registry.get("load")
            load_spec = StageSpec(use="load")
            plan.stages.append(PlannedStage(
                name="load", stage=load, spec=load_spec,
            ))
            available_artifacts.update(load.info.produces)
        except KeyError:
            available_artifacts.add("df")

        # Resolve each configured stage
        for raw_spec in config.stages:
            if isinstance(raw_spec, str):
                spec = StageSpec(use=raw_spec)
            else:
                spec = raw_spec

            stage_obj = registry.get(spec.use)
            name = spec.name or stage_obj.info.name

            for dep in stage_obj.info.consumes:
                if dep not in available_artifacts:
                    raise WiringError(
                        f"Stage '{name}' consumes '{dep}' but no prior stage "
                        f"produces it. Available: {sorted(available_artifacts)}"
                    )

            plan.stages.append(PlannedStage(
                name=name,
                stage=stage_obj,
                spec=spec,
                config=spec.config,
            ))
            available_artifacts.update(stage_obj.info.produces)

        return plan
