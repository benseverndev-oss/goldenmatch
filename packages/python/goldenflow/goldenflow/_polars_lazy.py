"""Lazy Polars proxy — Phase 4a of the Polars eviction (make Polars optional).

``from goldenflow._polars_lazy import pl`` gives a stand-in that imports Polars on
the FIRST attribute access, not at import time. Every ``pl.col(...)`` /
``pl.Series(...)`` / ``pl.Utf8`` runtime use in the transform + engine modules keeps
working unchanged, but ``import goldenflow`` no longer eagerly imports Polars (the
transform registry pulls every transform module, so a single top-level ``import
polars`` anywhere in that chain loaded Polars for every user, including those who
only touch the Polars-free columnar path).

Safe because:
- Type annotations are strings (`from __future__ import annotations` in every module
  that uses the proxy), so signatures like ``-> pl.Expr`` never trigger the import.
- There is no module-level ``pl.`` execution and no ``def f(x=pl.X)`` default arg
  (audited), so nothing evaluates ``pl.`` at import time.
- Attribute access returns the REAL Polars object (``pl.DataFrame`` is the actual
  class), so ``isinstance(x, pl.DataFrame)`` and ``return_dtype=pl.Utf8`` behave
  identically to a direct ``import polars as pl``.

This lands while ``polars`` is still a hard dependency (a pure refactor, no behavior
change); it is the enabler for later Phase-4 steps that move ``polars`` to an
optional ``[polars]`` extra.
"""
from __future__ import annotations

from typing import Any


class _LazyPolars:
    """A proxy that forwards attribute access to ``polars``, importing it on first
    use and caching the module thereafter."""

    __slots__ = ("_mod",)

    def __init__(self) -> None:
        self._mod: Any = None

    def __getattr__(self, name: str) -> Any:
        # `_mod` is a slot (set to None in __init__), so reading it here never
        # re-enters __getattr__; only genuine polars attributes reach this path.
        mod = self._mod
        if mod is None:
            import polars as _polars

            self._mod = mod = _polars
        return getattr(mod, name)


pl = _LazyPolars()
"""Module-level singleton — import as ``from goldenflow._polars_lazy import pl``."""
