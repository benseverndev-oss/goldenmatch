"""Local OSS-LLM lane config seams -- wheel-free (no Ollama, no network)."""
from __future__ import annotations

import io
import json

import goldenmatch.embeddings.providers as providers


class _Resp:  # context-manager shim for urlopen
    def __init__(self, fh):
        self._fh = fh

    def __enter__(self):
        return self._fh

    def __exit__(self, *a):
        return False


def _capture_embed_url(monkeypatch) -> dict:
    captured: dict = {}

    def _fake_urlopen(req):
        captured["url"] = req.full_url
        body = json.dumps({"data": [{"index": 0, "embedding": [0.1, 0.2]}]}).encode()
        return _Resp(io.BytesIO(body))

    monkeypatch.setattr(providers.urllib.request, "urlopen", _fake_urlopen)
    return captured


def test_embed_url_defaults_to_openai(monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    captured = _capture_embed_url(monkeypatch)
    providers.OpenAIProvider(model="m").embed(["hello"])
    assert captured["url"] == "https://api.openai.com/v1/embeddings"


def test_embed_url_empty_base_is_treated_as_unset(monkeypatch):
    # an empty env (how the workflow expresses "not local") must fall back to the OpenAI default,
    # NOT build "/embeddings".
    monkeypatch.setenv("OPENAI_BASE_URL", "")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    captured = _capture_embed_url(monkeypatch)
    providers.OpenAIProvider(model="m").embed(["hello"])
    assert captured["url"] == "https://api.openai.com/v1/embeddings"


def test_embed_url_honors_base_url(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "ollama")
    captured = _capture_embed_url(monkeypatch)
    providers.OpenAIProvider(model="m").embed(["hi"])
    assert captured["url"] == "http://localhost:11434/v1/embeddings"


def test_embed_url_strips_trailing_slash(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1/")
    monkeypatch.setenv("OPENAI_API_KEY", "ollama")
    captured = _capture_embed_url(monkeypatch)
    providers.OpenAIProvider(model="m").embed(["hi"])
    assert captured["url"] == "http://localhost:11434/v1/embeddings"


def _goldengraph_chat_model(eng) -> str:
    return eng._llm._inner.model  # GoldenGraphQAEngine -> _CountingLLM -> OpenAIClient


def test_build_engine_reads_openai_model_env(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "qwen2.5:7b-instruct")
    from erkgbench.qa_e2e import run_qa_e2e

    eng = run_qa_e2e._build_engine("goldengraph")
    assert _goldengraph_chat_model(eng) == "qwen2.5:7b-instruct"


def test_build_engine_default_model_when_unset(monkeypatch):
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    from erkgbench.qa_e2e import run_qa_e2e

    eng = run_qa_e2e._build_engine("goldengraph")
    assert _goldengraph_chat_model(eng) == "gpt-4o-mini"


def test_build_engine_empty_model_treated_as_unset(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "")
    from erkgbench.qa_e2e import run_qa_e2e

    eng = run_qa_e2e._build_engine("goldengraph")
    assert _goldengraph_chat_model(eng) == "gpt-4o-mini"
