"""infermap scorer registry and helpers."""
from __future__ import annotations

from collections.abc import Callable

from infermap.types import FieldInfo, ScorerResult

from .alias import AliasScorer
from .base import Scorer
from .exact import ExactScorer
from .fuzzy_name import FuzzyNameScorer
from .initialism import InitialismScorer
from .llm import LLMScorer
from .pattern_type import PatternTypeScorer
from .profile import ProfileScorer

_REGISTRY: dict[str, Scorer] = {}

# --- Cross-language parity surface (parity/infermap.yaml) -------------------
# The full set of built-in scorer identities (the `.name` of every scorer class),
# mirrored 1:1 by the TS registry (packages/typescript/infermap/src/core/scorers/
# registry.ts SCORER_NAMES). This is the `scorers` surface the api_parity gate
# compares Python<->TS. Derived from the class tuple so it can't drift from the
# classes themselves.
_ALL_SCORER_CLASSES: tuple[type, ...] = (
    ExactScorer,
    AliasScorer,
    PatternTypeScorer,
    ProfileScorer,
    FuzzyNameScorer,
    InitialismScorer,
    LLMScorer,
)
SCORER_NAMES: frozenset[str] = frozenset(c.name for c in _ALL_SCORER_CLASSES)

# The scorers backed by an `infermap-core` Rust kernel (the reference fast path,
# fed to Python via infermap-native and to TS via infermap-wasm -- see
# _native_loader._COMPONENT_SYMBOLS and core/wasm/backend.ts InfermapBackend).
# STATIC (independent of whether the wheel is built) -- mirrors goldenmatch's
# `_NATIVE_SCORER_IDS`. Every scorer NOT listed here is a pure-language fallback
# and MUST be classified in parity/infermap.yaml scorer_kernels_deferred (the
# check_scorer_coverage floor). The value is the kernel symbol (== the
# _native_loader component name) for traceability.
SCORER_KERNELS: dict[str, str] = {
    "ExactScorer": "exact_score",
    "FuzzyNameScorer": "fuzzy_name_score",
    "InitialismScorer": "initialism_score",
    "PatternTypeScorer": "pattern_match_types",
    "ProfileScorer": "profile_score",
}
assert set(SCORER_KERNELS) <= SCORER_NAMES, "SCORER_KERNELS must be a subset of SCORER_NAMES"


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
    "SCORER_NAMES",
    "SCORER_KERNELS",
    "_REGISTRY",
    "_FunctionScorer",
]
