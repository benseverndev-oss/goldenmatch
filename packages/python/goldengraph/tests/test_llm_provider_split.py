"""GOLDENGRAPH_LLM_BASE_URL / GOLDENGRAPH_LLM_API_KEY route goldengraph's CHAT client
to a separate provider (e.g. OpenRouter) WITHOUT moving the embedder, which reads the
generic OPENAI_* env. Unset -> byte-identical to the OPENAI_*-only behavior.

Constructs the REAL openai client (no network at construction) to read the resolved
base_url/api_key, so `openai` must be importable (the engine lanes install it)."""
import pytest

from goldengraph.llm import OpenAIClient

openai = pytest.importorskip("openai")

_OR = "https://openrouter.ai/api/v1"


def _resolved(monkeypatch, env: dict):
    for k in ("GOLDENGRAPH_LLM_BASE_URL", "GOLDENGRAPH_LLM_API_KEY",
              "OPENAI_BASE_URL", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    client = OpenAIClient(model="m")._ensure_client()
    return str(client.base_url), client.api_key


def test_chat_env_overrides_openai(monkeypatch):
    # GOLDENGRAPH_LLM_* wins for the chat client even when OPENAI_* is also set
    # (OPENAI_* stays pointed at OpenAI for the embedder).
    base, key = _resolved(monkeypatch, {
        "GOLDENGRAPH_LLM_BASE_URL": _OR,
        "GOLDENGRAPH_LLM_API_KEY": "or-key",
        "OPENAI_BASE_URL": "",  # the bench's OpenAI-API sentinel (empty)
        "OPENAI_API_KEY": "oa-key",
    })
    assert base.rstrip("/") == _OR
    assert key == "or-key"


def test_falls_back_to_openai_env_when_chat_unset(monkeypatch):
    # No GOLDENGRAPH_LLM_* -> byte-identical to the prior OPENAI_*-only path.
    base, key = _resolved(monkeypatch, {
        "OPENAI_BASE_URL": "https://api.openai.com/v1",
        "OPENAI_API_KEY": "oa-key",
    })
    assert base.rstrip("/") == "https://api.openai.com/v1"
    assert key == "oa-key"


def test_empty_openai_base_url_defaults(monkeypatch):
    # The empty-string OPENAI_BASE_URL sentinel must still yield the real OpenAI URL
    # (the SDK treats '' as an invalid literal URL), with no chat override present.
    base, key = _resolved(monkeypatch, {
        "OPENAI_BASE_URL": "",
        "OPENAI_API_KEY": "oa-key",
    })
    assert base.rstrip("/") == "https://api.openai.com/v1"
    assert key == "oa-key"
