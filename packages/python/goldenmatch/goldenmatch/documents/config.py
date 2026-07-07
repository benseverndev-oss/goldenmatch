"""Backend resolution + fail-fast validation for document ingest."""
from __future__ import annotations

import os

from goldenmatch.documents.extractor import Extractor
from goldenmatch.documents.vlm_backend import VLMExtractor

# Personal key first (see the openai-api-key memory: OPENAI_API_KEY may be work-scoped).
_KEY_ENV_ORDER = ("OPENAI_API_KEY_PERSONAL", "OPENAI_API_KEY")


def resolve_api_key() -> str:
    key = next((os.environ[e] for e in _KEY_ENV_ORDER if os.environ.get(e)), None)
    if not key:
        raise ValueError("no OpenAI API key found; set OPENAI_API_KEY_PERSONAL "
                         "(or OPENAI_API_KEY)")
    return key


def resolve_extractor(backend: str, model: str) -> Extractor:
    if backend != "vlm":
        raise ValueError(f"unknown backend: {backend!r} (Phase 1 supports 'vlm')")
    return VLMExtractor(api_key=resolve_api_key(), model=model)
