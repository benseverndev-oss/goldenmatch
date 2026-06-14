"""Tests for the local llama.cpp backend (embeddings + LLM base_url override)."""

from __future__ import annotations

import importlib.util
import json
import os
import urllib.request

import pytest

_HAS_LLAMA = importlib.util.find_spec("llama_cpp") is not None
_GGUF = os.environ.get("GOLDENMATCH_LLAMA_GGUF", "")


# ---- LLM base_url override (no network) ---------------------------------------

def test_openai_base_url_default(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    from goldenmatch.core.llm_scorer import _openai_base_url

    assert _openai_base_url() == "https://api.openai.com/v1"


def test_openai_base_url_override(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_LLM_BASE_URL", "http://localhost:8080/v1/")
    from goldenmatch.core.llm_scorer import _openai_base_url

    assert _openai_base_url() == "http://localhost:8080/v1"  # trailing slash stripped


def test_detect_provider_local_endpoint(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("GOLDENMATCH_LLM_BASE_URL", "http://localhost:8080/v1")
    from goldenmatch.core.llm_scorer import _detect_provider

    prov, key = _detect_provider()
    assert prov == "openai" and key  # stub key, no real OPENAI_API_KEY needed


def test_call_openai_hits_override_url(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_LLM_BASE_URL", "http://localhost:9999/v1")
    captured = {}

    class _Resp:
        def read(self):
            return json.dumps(
                {"choices": [{"message": {"content": "MATCH"}}],
                 "usage": {"prompt_tokens": 5, "completion_tokens": 1}}
            ).encode()

    def _fake_urlopen(req, timeout=30):
        captured["url"] = req.full_url
        captured["auth"] = req.headers.get("Authorization")
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    from goldenmatch.core.llm_scorer import _call_openai

    text, pin, pout = _call_openai("decide", "sk-local", "qwen2.5", max_tokens=8)
    assert captured["url"] == "http://localhost:9999/v1/chat/completions"
    assert text == "MATCH" and pin == 5 and pout == 1


# ---- embedder routing (no model load) -----------------------------------------

def test_get_embedder_routes_llama_lazily(monkeypatch):
    # Construction must NOT load the model (lazy) — a bogus path is fine here.
    monkeypatch.setenv("GOLDENMATCH_LLAMA_GGUF", "/nonexistent/model.gguf")
    from goldenmatch.core.embedder import _build_embedder, _ProviderEmbedder

    e = _build_embedder("llama")
    assert isinstance(e, _ProviderEmbedder)
    assert e.model_name == "llama:model.gguf"


def test_llama_provider_requires_path(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_LLAMA_GGUF", raising=False)
    from goldenmatch.embeddings.providers import LlamaGGUFProvider

    with pytest.raises(ValueError, match="GGUF"):
        LlamaGGUFProvider(None)


# ---- live embedding quality (needs a real GGUF; skipped in CI) -----------------

@pytest.mark.skipif(
    not (_HAS_LLAMA and _GGUF and os.path.exists(_GGUF)),
    reason="needs llama-cpp-python + GOLDENMATCH_LLAMA_GGUF pointing at a real model",
)
def test_llama_embeddings_separate_dup_from_nondup():
    from goldenmatch.embeddings.providers import LlamaGGUFProvider

    p = LlamaGGUFProvider()
    v = p.embed(["John Smith", "Jon Smith", "Zarathustra Quux"])
    assert v.shape[0] == 3
    # L2-normalized -> rows are unit vectors
    import numpy as np

    assert np.allclose(np.linalg.norm(v, axis=1), 1.0, atol=1e-4)
    cos = v @ v.T
    # the typo'd duplicate is closer than the unrelated name
    assert cos[0, 1] > cos[0, 2]
