"""_make_judge follows the CHAT provider (GOLDENGRAPH_LLM_* -> OPENAI_*) so the judge
routes wherever the engine's chat goes (e.g. OpenRouter, dodging an OpenAI per-model
daily cap) instead of always hitting OpenAI. No live LLM -- patches openai.OpenAI."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import erkgbench.qa_e2e.run_qa_e2e as rq  # noqa: E402


def _patch_openai(monkeypatch) -> dict:
    captured: dict = {}

    class _FakeOpenAI:
        def __init__(self, base_url=None, api_key=None):
            captured["base_url"] = base_url
            captured["api_key"] = api_key

    monkeypatch.setattr("openai.OpenAI", _FakeOpenAI)
    return captured


def test_judge_routes_via_chat_provider(monkeypatch):
    cap = _patch_openai(monkeypatch)
    monkeypatch.setenv("GOLDENGRAPH_LLM_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("GOLDENGRAPH_LLM_API_KEY", "or-key")
    monkeypatch.setenv("OPENAI_API_KEY", "oa-key")  # embeddings key, must NOT win for chat
    assert rq._make_judge("openai/gpt-4o-mini") is not None
    assert cap["base_url"] == "https://openrouter.ai/api/v1"
    assert cap["api_key"] == "or-key"


def test_judge_falls_back_to_openai(monkeypatch):
    cap = _patch_openai(monkeypatch)
    for k in ("GOLDENGRAPH_LLM_BASE_URL", "GOLDENGRAPH_LLM_API_KEY", "OPENAI_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "oa-key")
    assert rq._make_judge("gpt-4o-mini") is not None
    assert cap["base_url"] == "https://api.openai.com/v1"
    assert cap["api_key"] == "oa-key"


def test_judge_none_without_any_key(monkeypatch):
    for k in ("GOLDENGRAPH_LLM_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert rq._make_judge("m") is None
