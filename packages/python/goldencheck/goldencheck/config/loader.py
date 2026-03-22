"""Load goldencheck.yml configuration."""
from __future__ import annotations
import logging
from pathlib import Path
import yaml
from goldencheck.config.schema import GoldenCheckConfig

logger = logging.getLogger(__name__)

def load_config(path: Path) -> GoldenCheckConfig | None:
    if not path.exists():
        return None
    try:
        with open(path) as f:
            data = yaml.safe_load(f)
        if data is None:
            return GoldenCheckConfig()
        return GoldenCheckConfig(**data)
    except Exception as e:
        logger.error("Failed to load %s: %s", path, e)
        raise
