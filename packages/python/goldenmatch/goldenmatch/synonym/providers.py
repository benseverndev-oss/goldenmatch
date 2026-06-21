"""Per-domain SynonymModel provider registry.

Mirrors `goldenmatch.embeddings.providers.resolve_provider`: a domain name resolves
to a registered `SynonymModel`, or the `StubSynonymModel` default (never raises).
GS2 registers a trained `drug` model; GS1 ships only the stub.
"""

from __future__ import annotations

import threading

from .model import StubSynonymModel, SynonymModel

_REGISTRY: dict[str, SynonymModel] = {}
_LOCK = threading.Lock()
_STUB = StubSynonymModel()


def register_synonym_model(domain: str, model: SynonymModel) -> None:
    """Register a trained model for `domain` (last write wins)."""
    with _LOCK:
        _REGISTRY[domain] = model


def resolve_synonym_model(domain: str) -> SynonymModel:
    """The model for `domain`, or the stub default for an unregistered domain."""
    with _LOCK:
        return _REGISTRY.get(domain, _STUB)


def clear_synonym_models() -> None:
    """Drop all registrations (test isolation)."""
    with _LOCK:
        _REGISTRY.clear()
