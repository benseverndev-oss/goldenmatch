"""SynonymModel: the trained, per-domain semantic synonym scorer slot.

GS1 ships the protocol + a stub (returns None -> the scorer falls back to
Jaro-Winkler). GS2 plugs in a trained model (embed + cosine + calibrated
threshold) per domain. The model is intentionally decoupled from the scorer so a
domain can supply any `score(a, b) -> float | None` implementation.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SynonymModel(Protocol):
    """A learned synonym scorer for one domain.

    `score(a, b)` returns a similarity in [0, 1], or `None` when the model has no
    opinion (caller then falls back to a string scorer). Implementations should be
    deterministic for a fixed model.
    """

    def score(self, a: str, b: str) -> float | None: ...


class StubSynonymModel:
    """The GS1 default: no learned signal. Returns `None` so `SynonymScorer`
    degrades to Jaro-Winkler. Replaced per-domain by a trained model in GS2."""

    def score(self, a: str, b: str) -> float | None:  # noqa: ARG002 - no signal by design
        return None
