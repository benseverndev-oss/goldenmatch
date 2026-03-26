"""YAML config loading and normalization."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from goldenpipe.models.config import PipelineConfig, StageSpec


def load_config(path: str) -> PipelineConfig:
    """Load and validate a pipeline config from YAML."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(p) as f:
        raw: dict[str, Any] = yaml.safe_load(f)

    # Validate that stages is a list
    raw_stages = raw.get("stages", [])
    if not isinstance(raw_stages, list):
        raise ValueError(f"'stages' must be a list, got: {type(raw_stages).__name__}")

    # Normalize bare strings to StageSpec
    normalized_stages: list[StageSpec | str] = []
    for s in raw_stages:
        if isinstance(s, str):
            normalized_stages.append(StageSpec(use=s))
        elif isinstance(s, dict):
            if "use" not in s and "name" not in s:
                raise ValueError(f"Stage spec must have 'use' field: {s}")
            normalized_stages.append(StageSpec(**s))
        else:
            raise ValueError(f"Invalid stage spec: {s}")

    raw["stages"] = normalized_stages
    return PipelineConfig(**raw)
