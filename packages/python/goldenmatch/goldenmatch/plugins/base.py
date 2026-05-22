"""Base protocols for GoldenMatch plugins.

Plugin authors implement these protocols and register via entry points:

    [project.entry-points."goldenmatch.plugins.scorer"]
    my_scorer = "my_package.scorers:MyScorer"

Signatures match the runtime contracts in ``goldenmatch.core.scorer`` and
``goldenmatch.utils.transforms``. ``runtime_checkable`` lets the registry
``isinstance``-check at bind time, so a duck-typed implementation missing
a required method fails at registration rather than deep in a scoring loop.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import numpy as np
import polars as pl


@runtime_checkable
class ScorerPlugin(Protocol):
    """Plugin protocol for custom field scorers."""

    name: str

    def score_pair(self, val_a: str | None, val_b: str | None) -> float | None:
        """Score two field values. Returns ``None`` if either is ``None``.

        Called from ``goldenmatch.core.scorer.score_field`` for pair-by-pair
        scoring and as a fallback inside ``_fuzzy_score_matrix`` when the
        plugin doesn't expose ``score_matrix``.
        """
        ...


@runtime_checkable
class VectorizedScorerPlugin(ScorerPlugin, Protocol):
    """Optional extension: scorers that can produce an NxN similarity matrix
    in a single vectorized call. ``_fuzzy_score_matrix`` picks this up via
    ``getattr(plugin, "score_matrix", None)`` and avoids the O(N^2) Python
    double-loop on the hot path."""

    def score_matrix(self, values: list[str | None]) -> np.ndarray:
        """Return an NxN ``float32`` similarity matrix for ``values``.

        Symmetric (``output[i,j] == output[j,i]``); diagonals should be the
        scorer's value for ``score_pair(v, v)``. ``None`` entries are
        coerced to ``""`` by the caller before invocation.
        """
        ...


@runtime_checkable
class TransformPlugin(Protocol):
    """Plugin protocol for custom field transforms."""

    name: str

    def transform(self, value: str | None) -> str | None:
        """Transform a single value. Returns ``None`` iff ``value`` is ``None``.

        Called from ``goldenmatch.utils.transforms.apply_transform``'s
        plugin fallthrough.
        """
        ...


@runtime_checkable
class ConnectorPlugin(Protocol):
    """Plugin protocol for data source/sink connectors."""

    name: str

    def read(self, config: dict) -> pl.LazyFrame:
        """Read data from external source."""
        ...

    def write(self, df: pl.DataFrame, config: dict) -> None:
        """Write data to external sink."""
        ...


@runtime_checkable
class GoldenStrategyPlugin(Protocol):
    """Plugin protocol for custom golden record merge strategies.

    Plugins register via entry points::

        [project.entry-points."goldenmatch.plugins.golden_strategy"]
        legal_priority = "my_pkg.strategies:LegalPriorityStrategy"

    Users opt in via ``GoldenFieldRule(strategy="custom:legal_priority")``.
    The dispatcher in ``core/golden.py::merge_field`` looks up the
    plugin by name (after the ``custom:`` prefix), then calls ``merge``
    with all available signals. Plugins ignore kwargs they don't need.

    Spec: ``docs/superpowers/specs/2026-05-22-golden-strategy-plugin-slot-design.md``
    """

    name: str

    def merge(
        self,
        values: list,
        *,
        sources: list[str] | None = None,
        dates: list | None = None,
        quality_weights: list[float] | None = None,
        pair_scores: dict[tuple[int, int], float] | None = None,
        rule_kwargs: dict | None = None,
    ) -> Any:
        """Merge cluster member values into one survivor.

        Returns either ``(value, confidence)`` -- the dispatcher fills
        ``idx=0`` -- or ``(value, confidence, idx)`` for plugins that
        want to surface provenance.

        ``rule_kwargs`` carries the per-field ``GoldenFieldRule``'s
        configuration (``date_column``, ``source_priority``, or any
        custom keys the plugin defines) so the plugin can read YAML
        settings without sniffing global state.

        Exceptions raised here are caught by the dispatcher and
        fall back to ``most_complete`` with a WARNING log. To opt
        into strict mode, set ``GOLDENMATCH_GOLDEN_STRATEGY_STRICT=1``.
        """
        ...
