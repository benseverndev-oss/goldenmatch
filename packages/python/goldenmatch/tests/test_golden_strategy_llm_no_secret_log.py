"""The LLM strategy picker must never log the API key (CodeQL #302).

``_default_llm_caller`` pulls ``(provider, api_key)`` from the llm_scorer and
hands the key to ``_call_openai`` / ``_call_anthropic``. If that call raises an
exception whose text echoes the request (api keys frequently end up in HTTP
error messages), logging the full exception leaks the credential. The handler
must log only the exception *type*, not its message.
"""

from __future__ import annotations

import logging

import goldenmatch.core.llm_scorer as llm_scorer
from goldenmatch.core.golden_strategy_llm import _default_llm_caller

_SECRET = "sk-LEAKME-0123456789abcdef"


def test_default_llm_caller_does_not_log_api_key(monkeypatch, caplog) -> None:
    monkeypatch.setattr(llm_scorer, "_detect_provider", lambda: ("openai", _SECRET))

    def _boom(*args, **kwargs):
        # mimic an HTTP client that echoes the auth header into its error
        raise RuntimeError(f"401 Unauthorized: Bearer {_SECRET}")

    monkeypatch.setattr(llm_scorer, "_call_openai", _boom)

    with caplog.at_level(logging.DEBUG):
        result = _default_llm_caller("pick a strategy")

    assert result is None
    assert _SECRET not in caplog.text
