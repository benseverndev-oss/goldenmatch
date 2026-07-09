"""VLM-facing doctype collaborators: `VLMClassifier` (one classify call per doc)
and `VLMFallbackExtractor` (the generic path: suggest a schema then extract, 2
calls). Both put network I/O behind the injectable `Transport` so they test
offline. The deterministic prompt/parse kernels live in `classify.py`; these
classes only compose payloads + a transport call."""
from __future__ import annotations

from goldenmatch.documents._openai import (
    Transport,
    image_blocks,
    parse_message_text,
)
from goldenmatch.documents.classify import classify_prompt, parse_classify
from goldenmatch.documents.suggest import suggest_schema
from goldenmatch.documents.types import ClassifyResult, ExtractResult, PageImage
from goldenmatch.documents.vlm_backend import VLMExtractor


class VLMClassifier:
    """One OpenAI vision call per document: classify it into a doctype. Retries a
    transport failure like `suggest_schema`; a failure after the budget RAISES
    `ValueError` (classify is the routing gate -- a failed classify is not a
    silent 'generic')."""

    def __init__(self, *, transport: Transport, model: str = "gpt-4o",
                 max_attempts: int = 3):
        self._send = transport
        self._model = model
        self._max_attempts = max(1, max_attempts)

    def _payload(self, pages: list[PageImage]) -> dict:
        content = [{"type": "text", "text": classify_prompt()}] + image_blocks(pages)
        return {"model": self._model, "temperature": 0, "max_tokens": 200,
                "messages": [{"role": "user", "content": content}]}

    def classify(self, pages: list[PageImage]) -> ClassifyResult:
        payload = self._payload(pages)
        last = "no response"
        resp = None
        for _ in range(self._max_attempts):
            try:
                resp = self._send(payload)
                break
            except Exception as e:  # transport/network: retry (non-deterministic)
                last = f"{type(e).__name__}: {e}"
        else:
            raise ValueError(f"classify failed: {last}")
        return parse_classify(parse_message_text(resp))  # raises ValueError on bad JSON


class VLMFallbackExtractor:
    """The generic fallback: suggest a schema from the doc, then extract against
    it (2 VLM calls) -- both over ONE shared transport. Used when a doc classifies
    as 'generic' or below the confidence threshold."""

    def __init__(self, *, transport: Transport, model: str = "gpt-4o"):
        self._send = transport
        self._model = model

    def suggest_and_extract(self, pages: list[PageImage]) -> ExtractResult:
        schema = suggest_schema(pages, transport=self._send, model=self._model)
        # transport is provided, so api_key is never consulted (placeholder ok).
        return VLMExtractor(api_key="", model=self._model,
                            transport=self._send).extract(pages, schema)
