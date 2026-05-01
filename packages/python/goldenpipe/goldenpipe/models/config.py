"""Pipeline configuration models (Pydantic)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class StageSpec(BaseModel):
    """Configuration for a single pipeline stage."""
    name: str | None = None
    use: str
    needs: list[str] = []
    skip_if: str | None = None
    on_error: Literal["continue", "abort"] = "continue"
    config: dict[str, Any] = {}


class PipelineConfig(BaseModel):
    """Top-level pipeline configuration."""
    pipeline: str
    source: str | None = None
    output: str | None = None
    stages: list[StageSpec | str]
    decisions: list[str] = []
