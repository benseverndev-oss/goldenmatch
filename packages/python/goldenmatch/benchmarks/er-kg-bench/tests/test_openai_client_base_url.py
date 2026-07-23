"""Guarded OpenAI client construction (`text_rag.make_openai_client`).

The paid head_to_head lane sets OPENAI_BASE_URL to the EMPTY string (chat + embeddings
both to OpenAI directly). A bare `OpenAI()` uses that empty value verbatim, producing a
protocol-less URL that fails EVERY request with httpx.UnsupportedProtocol -- on the
embedding path this silently collapsed goldengraph's hybrid retrieval to entity-only.
This locks the empty -> OpenAI-default fall-through while honoring a real (local) base_url.
Pure, no network (constructs the client; never calls it)."""
from __future__ import annotations

from urllib.parse import urlparse


def _host(client) -> str:
    # Compare the parsed hostname exactly (not a substring/startswith) so the assertion
    # can't be satisfied by an unexpected host embedded elsewhere in the URL.
    return urlparse(str(client.base_url)).hostname or ""


def test_empty_base_url_falls_through_to_openai_default(monkeypatch):
    from erkgbench.qa_e2e.engines.text_rag import make_openai_client

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_BASE_URL", "")  # the head_to_head lane's setting
    client = make_openai_client()
    # An empty base_url would have become a protocol-less URL; the guard yields the default.
    assert _host(client) == "api.openai.com"


def test_nonempty_base_url_is_honored(monkeypatch):
    from erkgbench.qa_e2e.engines.text_rag import make_openai_client

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")  # local nomic/Ollama lane
    client = make_openai_client()
    parsed = urlparse(str(client.base_url))
    assert parsed.hostname == "localhost"
    assert parsed.port == 11434


def test_unset_base_url_uses_openai_default(monkeypatch):
    from erkgbench.qa_e2e.engines.text_rag import make_openai_client

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    client = make_openai_client()
    assert _host(client) == "api.openai.com"
