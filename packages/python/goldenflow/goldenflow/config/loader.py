from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from goldenflow.config.schema import GoldenFlowConfig


def load_config(path: Path) -> GoldenFlowConfig:
    if not path.exists():
        return GoldenFlowConfig()
    with open(path) as f:
        data: dict[str, Any] = yaml.safe_load(f) or {}
    return GoldenFlowConfig(**data)


def save_config(config: GoldenFlowConfig, path: Path) -> None:
    data = config.model_dump(exclude_defaults=True)
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)


def merge_configs(
    file_config: GoldenFlowConfig, cli_overrides: GoldenFlowConfig
) -> GoldenFlowConfig:
    base = file_config.model_dump()
    overrides = cli_overrides.model_dump(exclude_defaults=True)
    base.update(overrides)
    return GoldenFlowConfig(**base)
