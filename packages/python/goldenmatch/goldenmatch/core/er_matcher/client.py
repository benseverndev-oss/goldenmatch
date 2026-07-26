"""Local ER-matcher client (P6, Path A).

Talks to an OpenAI-compatible ``/v1/chat/completions`` endpoint (a local
llama.cpp / llamafile / vLLM server serving the pinned GGUF) using the shared
prompt contract, and returns a parsed verdict. This is the per-pair primitive the
eval harness (P5) drives and the future in-process boost can reuse; it exposes
``confidence`` (the batch ``llm_score_pairs`` path only returns a bool).

The HTTP transport is injectable (``post_fn``) so the client is fully testable
without a running server -- the default transport is stdlib urllib (no new dep).
Any transport/parse failure yields ``None`` = ABSTAIN, never a raise.
"""
from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable
from typing import Any

from goldenmatch.core.er_matcher.prompt import build_chat, parse_verdict

# post_fn(url, payload_dict, timeout) -> response_dict (OpenAI chat-completions shape)
PostFn = Callable[[str, dict[str, Any], float], dict[str, Any]]


def _urllib_post(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer sk-local"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (local server)
        return json.loads(resp.read().decode("utf-8"))


def _extract_text(resp: dict[str, Any]) -> str | None:
    try:
        return resp["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None


class LocalMatcherClient:
    """Score record pairs against a local OpenAI-compatible ER-matcher server."""

    def __init__(
        self,
        base_url: str = "http://localhost:8080/v1",
        model: str = "goldenmatch-er-matcher",
        *,
        post_fn: PostFn | None = None,
        timeout: float = 30.0,
        temperature: float = 0.0,
        max_tokens: int = 96,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._post = post_fn or _urllib_post
        self.timeout = timeout
        self.temperature = temperature
        self.max_tokens = max_tokens

    def score_pair(self, a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any] | None:
        """Return ``{match, confidence, reason}`` or ``None`` (abstain) on any failure."""
        payload = {
            "model": self.model,
            "messages": build_chat(a, b),
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        try:
            resp = self._post(f"{self.base_url}/chat/completions", payload, self.timeout)
        except Exception:
            return None  # network/server error -> abstain (fallback contract)
        text = _extract_text(resp)
        if text is None:
            return None
        return parse_verdict(text)

    def score_pairs(
        self, pairs: list[tuple[dict[str, Any], dict[str, Any]]]
    ) -> list[dict[str, Any] | None]:
        return [self.score_pair(a, b) for a, b in pairs]
