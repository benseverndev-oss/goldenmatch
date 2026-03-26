"""Tests for global and project settings persistence."""
from __future__ import annotations

from pathlib import Path

from goldencheck.config.settings import (
    DEFAULT_SETTINGS,
    global_settings_path,
    load_settings,
    save_settings,
)


def test_default_settings():
    assert DEFAULT_SETTINGS["sample_size"] == 100_000
    assert DEFAULT_SETTINGS["severity_threshold"] == "warning"
    assert DEFAULT_SETTINGS["fail_on"] == "error"
    assert DEFAULT_SETTINGS["domain"] is None
    assert DEFAULT_SETTINGS["llm_provider"] == "anthropic"
    assert DEFAULT_SETTINGS["llm_boost"] is False


def test_save_and_load(tmp_path: Path):
    settings_file = tmp_path / "settings.yaml"
    custom = {**DEFAULT_SETTINGS, "sample_size": 50_000, "domain": "healthcare"}
    save_settings(custom, path=settings_file)

    loaded = load_settings(path=settings_file)
    assert loaded["sample_size"] == 50_000
    assert loaded["domain"] == "healthcare"
    assert loaded["fail_on"] == "error"  # unchanged default


def test_load_missing_returns_defaults(tmp_path: Path):
    missing = tmp_path / "nonexistent" / "settings.yaml"
    loaded = load_settings(path=missing)
    assert loaded == DEFAULT_SETTINGS


def test_global_settings_path():
    path = global_settings_path()
    assert path == Path.home() / ".goldencheck" / "settings.yaml"
    assert path.name == "settings.yaml"
