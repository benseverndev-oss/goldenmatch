"""Write goldencheck.yml configuration."""
from __future__ import annotations
from pathlib import Path
import yaml
from goldencheck.config.schema import GoldenCheckConfig

def save_config(config: GoldenCheckConfig, path: Path) -> None:
    data = config.model_dump(exclude_none=True, exclude_defaults=False)
    if not data.get("columns"):
        data.pop("columns", None)
    if not data.get("relations"):
        data.pop("relations", None)
    if not data.get("ignore"):
        data.pop("ignore", None)
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)
