"""GET / PUT /api/v1/settings — persisted user-level preferences.

Settings live at the OS-standard per-user config dir (see web/settings.py).
The GET response folds in live env state (``llm_keys_present``) so the UI
can render "LLM available" without a separate roundtrip; that view is
computed fresh on every read and never persisted.
"""
from __future__ import annotations

from fastapi import APIRouter

from goldenmatch.web.settings import (
    WebSettings,
    load_settings,
    save_settings,
    settings_path,
)

router = APIRouter(prefix="/api/v1/settings")


@router.get("")
def get_settings() -> dict:
    base = load_settings()
    out = WebSettings.with_env_status(base)
    out["_path"] = str(settings_path())
    return out


@router.put("")
def put_settings(payload: WebSettings) -> dict:
    p = save_settings(payload)
    out = WebSettings.with_env_status(payload)
    out["_path"] = str(p)
    return out
