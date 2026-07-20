"""OpenAIClient must not be poisoned by an empty-but-present OPENAI_BASE_URL.

The bench workflow sets OPENAI_BASE_URL='' on the OpenAI-API path (only non-empty
for a local Ollama run). The openai SDK treats an empty-string base_url as a literal
invalid URL -> APIConnectionError on every call, and passing base_url=None does not
help (the SDK re-reads the empty env var). The client must fall back to the default
endpoint when the env var is empty, and honor a real base_url when one is set."""
from __future__ import annotations

import pytest

pytest.importorskip("openai")  # the [openai] extra isn't installed on every lane

from goldengraph.llm import OpenAIClient  # noqa: E402


def test_empty_base_url_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "")  # the CI OpenAI-API path
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    client = OpenAIClient(model="gpt-4o-mini")._ensure_client()
    assert "api.openai.com" in str(client.base_url)


def test_unset_base_url_uses_default(monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    client = OpenAIClient(model="gpt-4o-mini")._ensure_client()
    assert "api.openai.com" in str(client.base_url)


def test_real_base_url_is_honored(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")  # local Ollama
    monkeypatch.setenv("OPENAI_API_KEY", "ollama")
    client = OpenAIClient(model="qwen")._ensure_client()
    assert "localhost:11434" in str(client.base_url)
