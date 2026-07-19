"""Pipeline configuration models (Pydantic)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class StageSpec(BaseModel):
    """Configuration for a single pipeline stage."""
    name: str | None = Field(
        default=None,
        description="Optional human-readable label for this stage, used in logs and as the key other stages reference in their needs list.",
    )
    use: str = Field(
        description="Identifier of the adapter that runs this stage, such as goldenmatch.dedupe, naming which suite tool to invoke.",
    )
    needs: list[str] = Field(
        default_factory=list,
        description="Names of stages that must finish before this one runs, defining the dependency order in the pipeline graph.",
    )
    skip_if: str | None = Field(
        default=None,
        description="Condition expression that, when it evaluates true at runtime, causes this stage to be skipped rather than executed.",
    )
    on_error: Literal["continue", "abort"] = Field(
        default="continue",
        description="What to do when this stage raises an error: continue on to later stages or abort the whole pipeline.",
    )
    config: dict[str, Any] = Field(
        default_factory=dict,
        description="Free-form settings dictionary passed straight through to the named adapter to configure how the tool runs.",
    )


class PipelineConfig(BaseModel):
    """Top-level pipeline configuration."""
    pipeline: str = Field(
        description="Name of the pipeline, identifying this end-to-end configuration in runs and logs.",
    )
    source: str | None = Field(
        default=None,
        description="Optional default input location that stages read from when they do not specify their own source.",
    )
    output: str | None = Field(
        default=None,
        description="Optional default destination where the pipeline writes its final results.",
    )
    stages: list[StageSpec | str] = Field(
        description="Ordered list of stages to run, each given as a full StageSpec or a shorthand string naming the adapter to use.",
    )
    decisions: list[str] = Field(
        default_factory=list,
        description="Recorded pipeline decision entries that capture the choices and rationale behind this configuration.",
    )
