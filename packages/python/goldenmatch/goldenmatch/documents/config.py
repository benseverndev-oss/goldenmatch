"""Backend resolution + fail-fast validation for document ingest."""
from __future__ import annotations

import os

from goldenmatch.documents._openai import urllib_transport
from goldenmatch.documents.extractor import (
    Classifier,
    Extractor,
    FallbackExtractor,
    TemplateExtractor,
)
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
        raise ValueError(f"unsupported backend: {backend!r} (only 'vlm' is supported)")
    return VLMExtractor(api_key=resolve_api_key(), model=model)


def resolve_structured(
    backend: str, model: str
) -> tuple[Classifier, TemplateExtractor, FallbackExtractor]:
    """Build the three structured collaborators sharing ONE resolved transport
    (resolve the api key once, build one `urllib_transport`, pass to all three)."""
    if backend != "vlm":
        raise ValueError(f"unsupported backend: {backend!r} (only 'vlm' is supported)")
    # Imported lazily: vlm_classifier -> suggest -> config would cycle at import time.
    from goldenmatch.documents.structured import VLMTemplateExtractor
    from goldenmatch.documents.vlm_classifier import VLMClassifier, VLMFallbackExtractor

    transport = urllib_transport(resolve_api_key())
    return (
        VLMClassifier(transport=transport, model=model),
        VLMTemplateExtractor(transport=transport, model=model),
        VLMFallbackExtractor(transport=transport, model=model),
    )
