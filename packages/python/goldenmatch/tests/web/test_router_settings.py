"""GET / PUT /api/v1/settings — persisted user-level preferences.

Settings live at the OS-standard per-user config dir. Tests redirect that
location into tmp_path so a contributor's real settings.json isn't touched.
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Force settings.json into tmp_path for every test in this module.

    settings_path() resolves via APPDATA / XDG_CONFIG_HOME / Library — set
    the platform-appropriate var so settings_path() lands in tmp_path
    regardless of test host OS.
    """
    monkeypatch.setenv("APPDATA", str(tmp_path))  # Windows
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))  # Linux
    # macOS uses ~/Library directly — patch HOME so it lands in tmp.
    monkeypatch.setenv("HOME", str(tmp_path))
    yield


def test_get_settings_returns_defaults_when_unset(client):
    resp = client.get("/api/v1/settings")
    assert resp.status_code == 200
    body = resp.json()
    assert body["llm_boost_default"] is False
    assert body["llm_max_cost_usd"] == 0.05
    assert body["llm_max_calls"] == 500
    assert body["preview_sample_n"] == 1000
    assert "llm_keys_present" in body
    assert "openai" in body["llm_keys_present"]


def test_put_settings_persists(client):
    new = {
        "llm_boost_default": True,
        "llm_provider": "anthropic",
        "llm_max_cost_usd": 0.25,
        "llm_max_calls": 200,
        "review_band_lo": 0.6,
        "review_band_hi": 0.95,
        "preview_sample_n": 500,
    }
    resp = client.put("/api/v1/settings", json=new)
    assert resp.status_code == 200
    # Round-trip via GET.
    body = client.get("/api/v1/settings").json()
    assert body["llm_boost_default"] is True
    assert body["llm_provider"] == "anthropic"
    assert body["llm_max_cost_usd"] == 0.25
    assert body["preview_sample_n"] == 500


def test_put_settings_validates_bounds(client):
    bad = {"llm_max_cost_usd": -1.0, "llm_max_calls": 100, "preview_sample_n": 100,
           "llm_boost_default": False, "llm_provider": "openai",
           "review_band_lo": 0.5, "review_band_hi": 1.0}
    assert client.put("/api/v1/settings", json=bad).status_code == 422


def test_get_settings_reflects_env_for_keys(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    body = client.get("/api/v1/settings").json()
    assert body["llm_keys_present"]["openai"] is True
    assert body["llm_keys_present"]["anthropic"] is False
