"""GoldenSynonym — a trained, domain-aware synonym scorer (GS1: framework).

Importing this package registers the `synonym` scorer into the PluginRegistry, so
`scorer: synonym` resolves after `import goldenmatch`. GS1 ships the surface: the
default scorer degrades to Jaro-Winkler (empty table + stub model); GS2 registers a
per-domain alias table + trained model. Spec:
`docs/superpowers/specs/2026-06-20-goldensynonym-trained-synonym-scorer-design.md`.
"""

from __future__ import annotations

from goldenmatch.plugins.registry import PluginRegistry

from .model import StubSynonymModel, SynonymModel
from .providers import (
    clear_synonym_models,
    register_synonym_model,
    resolve_synonym_model,
)
from .scorer import SynonymScorer
from .table import SynonymTable

__all__ = [
    "SynonymScorer",
    "SynonymModel",
    "StubSynonymModel",
    "SynonymTable",
    "register_synonym_model",
    "resolve_synonym_model",
    "clear_synonym_models",
    "register_synonym_scorer",
]


def register_synonym_scorer() -> None:
    """Register the `synonym` scorer (idempotent; last write wins)."""
    PluginRegistry.instance().register_scorer("synonym", SynonymScorer())


register_synonym_scorer()
