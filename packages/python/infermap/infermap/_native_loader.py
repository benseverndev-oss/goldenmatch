"""Loader + gate for the optional ``infermap._native`` accelerator.

InferMap's first Rust cutover (Wave 1) ships ``infermap-core`` / ``infermap-native``.
This module is the one place the call sites read; mirrors
``goldenanalysis.core._native_loader`` / ``goldencheck.core._native_loader``.

``INFERMAP_NATIVE`` env:
- ``"0"``    -> force the Python path (never use native).
- ``"1"``    -> require native; raise if it isn't importable (CI parity lane).
- ``"auto"`` / unset -> reference mode: use native wherever the component's kernel
  symbol exists (``_COMPONENT_SYMBOLS`` / ``_has_symbol``); the pure-Python path is
  the lossy fallback.

The kernel is reachable two ways, tried in order:
  1. ``infermap._native``        -- the in-tree build (local dev / parity lane).
  2. ``infermap_native._native`` -- the ``infermap-native`` abi3 wheel.
"""

from __future__ import annotations

import os
from typing import Any

try:
    import infermap._native as _native  # pyright: ignore[reportMissingImports]
except Exception:  # noqa: BLE001 - any import/load failure falls back below
    try:
        from infermap_native import _native  # pyright: ignore[reportMissingImports]
    except Exception:  # noqa: BLE001 - neither path available -> pure Python
        _native = None


# Components whose native path has cleared parity and may run under
# ``INFERMAP_NATIVE=auto``. ``detect_domain`` joined after ``test_native_parity.py``
# proved byte-identical output. (detect is small-compute -- this is anti-drift /
# scaffold, not a perf claim; a new primitive joins only after the parity gate clears.)
_GATED_ON: frozenset[str] = frozenset(
    {
        "detect_domain",
        "exact_score",
        "fuzzy_name_score",
        "initialism_score",
        "profile_score",
        "pattern_match_types",
    }
)

# Component -> the native symbol that backs it (component name == symbol here). A
# component is only usable when its symbol is present on the loaded module.
_COMPONENT_SYMBOLS: dict[str, str] = {
    "detect_domain": "detect_domain",
    "exact_score": "exact_score",
    "fuzzy_name_score": "fuzzy_name_score",
    "initialism_score": "initialism_score",
    "profile_score": "profile_score",
    "pattern_match_types": "pattern_match_types",
}


def _has_symbol(component: str) -> bool:
    if _native is None:
        return False
    symbol = _COMPONENT_SYMBOLS.get(component)
    return symbol is not None and hasattr(_native, symbol)


def native_module() -> Any:
    """The imported ``infermap._native`` module, or ``None`` if unavailable."""
    return _native


def native_available() -> bool:
    return _native is not None


def native_enabled(component: str) -> bool:
    """Whether to use the native kernel for ``component`` on this call.

    Reference mode: native runs wherever the component's kernel symbol exists;
    ``_GATED_ON`` is retained as byte-exact sign-off documentation but no longer
    governs ``auto``. With ``INFERMAP_NATIVE=1`` and no built kernel, raises -- the
    require-native CI parity contract.
    """
    mode = os.environ.get("INFERMAP_NATIVE", "auto").lower()
    if mode == "0":
        return False
    if mode == "1":
        if _native is None:
            raise RuntimeError(
                "INFERMAP_NATIVE=1 but infermap._native is not built/importable"
            )
        return _has_symbol(component)
    return _native is not None and _has_symbol(component)
