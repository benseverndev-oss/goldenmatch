"""Global and project settings persistence."""
from __future__ import annotations

from pathlib import Path

import yaml

DEFAULT_SETTINGS: dict = {
    "sample_size": 100_000,
    "severity_threshold": "warning",
    "fail_on": "error",
    "domain": None,
    "llm_provider": "anthropic",
    "llm_boost": False,
}


def global_settings_path() -> Path:
    return Path.home() / ".goldencheck" / "settings.yaml"


def load_settings(path: Path | None = None) -> dict:
    settings = dict(DEFAULT_SETTINGS)
    if path is None:
        path = global_settings_path()
    if path.exists():
        with open(path) as f:
            user = yaml.safe_load(f) or {}
        settings.update(user)
    return settings


def save_settings(settings: dict, path: Path | None = None) -> None:
    if path is None:
        path = global_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(settings, f, default_flow_style=False, sort_keys=False)
