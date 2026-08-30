# goldenmatch/_polars_lazy.py
"""Lazy Polars proxy -- W0 of the Polars eviction (spec:
docs/superpowers/specs/2026-07-09-goldenmatch-polars-eviction-design.md).

``from goldenmatch._polars_lazy import pl`` gives a stand-in that imports Polars
on the FIRST attribute access, not at import time. Every runtime ``pl.`` use in
the swept modules keeps working unchanged, but ``import goldenmatch`` no longer
eagerly imports Polars.

All 112 module-level ``import polars as pl`` sites in the package are swept
onto this proxy (landed in this same wave). Every module swept onto it must
satisfy these invariants (verified to hold as of 2026-07-09):
- The module has ``from __future__ import annotations`` (string annotations
  never trigger the import).
- No module-level ``pl.`` execution (the two dtype-set constants in
  core/indicators.py were handled in this wave: ``_NON_IDENTITY_DTYPES``
  converted to a lazy ``lru_cache`` function, the dead ``_BOOLEAN_DTYPES``
  deleted outright) and no ``def f(x=pl.X)`` default arg.
- Attribute access returns the REAL Polars object, so ``isinstance(x,
  pl.DataFrame)`` and dtype identity behave identically to ``import polars``.

The ``TYPE_CHECKING`` branch makes pyright treat ``pl`` as the real module, so
static narrowing across the package is unchanged (a deliberate divergence from
the goldenflow/goldencheck template, which exposes ``Any``).
"""
from __future__ import annotations

import sys
from functools import lru_cache
from typing import TYPE_CHECKING, Any

__all__ = ["pl", "polars_available"]


class _LazyPolars:
    """Forwards attribute access to ``polars``, importing it on first use."""

    __slots__ = ("_mod",)

    def __init__(self) -> None:
        self._mod: Any = None

    def __getattr__(self, name: str) -> Any:
        # `_mod` is a slot (set in __init__), so reading it never re-enters
        # __getattr__; only genuine polars attributes reach this path.
        mod = self._mod
        if mod is None:
            import polars as _polars

            self._mod = mod = _polars
        return getattr(mod, name)


if TYPE_CHECKING:
    import polars as pl
else:
    pl = _LazyPolars()


@lru_cache(maxsize=1)
def polars_available() -> bool:
    """Whether ``import polars`` would succeed -- WITHOUT importing it.

    Polars is an optional extra (``pip install goldenmatch[polars]``), so a
    plain ``pip install goldenmatch`` has none. Any site that HAS a working
    arrow path and only reaches for polars to preserve a formatting contract
    needs to branch on availability up front: touching ``pl.anything`` to find
    out is what turned "no polars installed" into a crash on the zero-config
    CLI path.

    Cached -- the answer cannot change within a process.
    """
    mod = sys.modules.get("polars")
    if mod is not None:
        return True
    try:
        from importlib.util import find_spec

        return find_spec("polars") is not None
    except Exception:  # noqa: BLE001 -- a blocked/broken polars is an absent one
        return False
