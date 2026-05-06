"""Persisted user-level web UI preferences.

Lives at the standard OS config location:

  - Windows : ``%APPDATA%\\goldenmatch-ui\\settings.json``
  - Linux   : ``${XDG_CONFIG_HOME:-~/.config}/goldenmatch-ui/settings.json``
  - macOS   : ``~/Library/Application Support/goldenmatch-ui/settings.json``

Per-USER on purpose — settings follow the human, not the project. A
contributor cloning the repo doesn't pick up someone else's defaults.

What lives here: defaults that the user wants to survive reloads (LLM
boost on by default, cost caps, default review band, etc.).

What does NOT live here:

  - **API keys**. Always read from environment variables
    (``OPENAI_API_KEY`` / ``ANTHROPIC_API_KEY``) per OSS convention.
    The web UI surfaces whether a key is present in the current env;
    storing the key itself would be a hostile default for a tool that
    also writes to disk. Users with a project-local convention can
    keep their key in ``.testing/.env`` (per goldenmatch's existing
    pattern) and source it before launching ``goldenmatch serve-ui``.

  - **Per-project state** (which run is selected, etc.). That's UI
    state, not preference; it lives in the browser's localStorage
    or in the project directory itself.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

APP_NAME = "goldenmatch-ui"


def _config_dir() -> Path:
    """Return the per-user config dir, creating parents as needed.

    Resolves XDG / APPDATA / Library paths so contributors on every
    platform get a sane default. Fallback for unusual setups: ~/.config.
    """
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        if base:
            return Path(base) / APP_NAME
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / APP_NAME
    return Path.home() / ".config" / APP_NAME


def settings_path() -> Path:
    return _config_dir() / "settings.json"


class WebSettings(BaseModel):
    """User-level preferences that survive server restarts.

    All fields have sensible defaults so a fresh user on a fresh laptop
    gets a working tool without touching the settings page.
    """

    # LLM boost defaults
    llm_boost_default: bool = Field(
        default=False,
        description="When the workbench / project page render the LLM-boost toggle, default it to this value.",
    )
    llm_provider: str = Field(
        default="openai",
        description="Preferred LLM provider when both keys are present. One of {openai, anthropic}.",
    )
    llm_max_cost_usd: float = Field(
        default=0.05,
        ge=0.0,
        le=100.0,
        description="Per-run cap on LLM scorer spend. Mirrors the goldenmatch BudgetConfig default.",
    )
    llm_max_calls: int = Field(
        default=500,
        ge=1,
        le=100_000,
        description="Per-run cap on LLM scorer calls.",
    )

    # Review queue defaults — control what shows up in the inspector's review tab
    review_band_lo: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Default lower bound for the review queue's score band.",
    )
    review_band_hi: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Default upper bound for the review queue's score band.",
    )

    # Preview defaults
    preview_sample_n: int = Field(
        default=1000,
        ge=1,
        le=10_000,
        description="Default sample size for the workbench preview.",
    )

    # Surface env state on read so the UI can show "LLM available" without a separate call.
    # Computed fresh each GET — never persisted.
    @classmethod
    def with_env_status(cls, base: "WebSettings") -> dict[str, Any]:
        return {
            **base.model_dump(),
            "llm_keys_present": {
                "openai": bool(os.environ.get("OPENAI_API_KEY")),
                "anthropic": bool(os.environ.get("ANTHROPIC_API_KEY")),
            },
        }


def load_settings() -> WebSettings:
    """Read settings.json, or return defaults if missing / unreadable."""
    p = settings_path()
    if not p.exists():
        return WebSettings()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # Don't punish the user for a corrupt settings file — fall back
        # to defaults silently. Next save will overwrite cleanly.
        return WebSettings()
    return WebSettings.model_validate(raw)


def save_settings(settings: WebSettings) -> Path:
    """Write settings.json atomically. Returns the final path."""
    p = settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(settings.model_dump(), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(tmp, p)
    return p
