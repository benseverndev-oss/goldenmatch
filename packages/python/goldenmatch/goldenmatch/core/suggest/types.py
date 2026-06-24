"""Public types for the config-suggestion adapter."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class SuggestionsNativeRequired(RuntimeError):
    """Raised when the native kernel is required but not available.

    Install with: pip install goldenmatch[native]
    """


@dataclass
class Suggestion:
    """A single config suggestion returned by the native kernel.

    Mirrors the JSON shape produced by ``suggest_config`` in the Rust crate.

    Attributes:
        id: stable identifier for this suggestion (e.g. "raise_threshold:fuzzy_match").
        kind: broad category ("threshold", "scorer", "negative_evidence", ...).
        target: dotted path within the config the suggestion addresses
                (e.g. "matchkeys[0].threshold").
        current_value: the config value as it stands now (any JSON-safe type).
        proposed_value: the suggested replacement value.
        rationale: human-readable explanation of *why* this change is suggested.
        predicted_effect: short description of the expected outcome.
        confidence: 0.0-1.0 confidence in the suggestion.
        patch: machine-readable patch dict with at minimum an "op" key
               ("replace", "add", "remove") and a "path" key.
        evidence: structured evidence bag supporting the suggestion (scores,
                  rates, etc.) -- kernel-defined, may be empty.
    """

    id: str
    kind: str
    target: str
    current_value: Any
    proposed_value: Any
    rationale: str
    predicted_effect: str
    confidence: float
    patch: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)
