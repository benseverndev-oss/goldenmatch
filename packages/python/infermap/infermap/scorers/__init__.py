"""infermap scorer registry and helpers."""
from __future__ import annotations

from typing import Callable

from infermap.types import FieldInfo, ScorerResult

from .base import Scorer
from .exact import ExactScorer
from .alias import AliasScorer
from .pattern_type import PatternTypeScorer
from .profile import ProfileScorer
from .fuzzy_name import FuzzyNameScorer
from .initialism import InitialismScorer
from .llm import LLMScorer

_REGISTRY: dict[str, Scorer] = {}


def default_scorers() -> list[Scorer]:
    """Return the default ordered list of scorers."""
    return [
        ExactScorer(),
        AliasScorer(),
        PatternTypeScorer(),
        ProfileScorer(),
        FuzzyNameScorer(),
        InitialismScorer(),
    ]


class _FunctionScorer:
    """Wraps a plain function as a Scorer."""

    def __init__(
        self,
        fn: Callable[[FieldInfo, FieldInfo], ScorerResult | None],
        name: str,
        weight: float,
    ) -> None:
        self._fn = fn
        self.name = name
        self.weight = weight

    def score(self, source: FieldInfo, target: FieldInfo) -> ScorerResult | None:
        return self._fn(source, target)

    def __repr__(self) -> str:  # pragma: no cover
        return f"_FunctionScorer(name={self.name!r}, weight={self.weight})"


def scorer(name: str, weight: float = 1.0) -> Callable:
    """Decorator that registers a function as a named scorer.

    Usage::

        @scorer("my_scorer", weight=0.6)
        def my_scorer(source: FieldInfo, target: FieldInfo) -> ScorerResult | None:
            ...
    """

    def decorator(fn: Callable[[FieldInfo, FieldInfo], ScorerResult | None]) -> _FunctionScorer:
        wrapped = _FunctionScorer(fn, name=name, weight=weight)
        _REGISTRY[name] = wrapped
        return wrapped

    return decorator


__all__ = [
    "Scorer",
    "ExactScorer",
    "AliasScorer",
    "PatternTypeScorer",
    "ProfileScorer",
    "FuzzyNameScorer",
    "InitialismScorer",
    "LLMScorer",
    "default_scorers",
    "scorer",
    "_REGISTRY",
    "_FunctionScorer",
]
