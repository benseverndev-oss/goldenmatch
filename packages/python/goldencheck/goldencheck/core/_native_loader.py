"""Loader + gate for the optional ``goldencheck._native`` acceleration module.

The native extension (Rust/PyO3, built from
``packages/rust/extensions/goldencheck-native``) is an *optional accelerator*:
when it isn't importable, the pure-Python paths run unchanged. Selection is
centralised here so every call site reads one gate. Mirrors goldenmatch's
``goldenmatch.core._native_loader``.

``GOLDENCHECK_NATIVE`` env:
- ``"0"``    -> force the Python path (never use native).
- ``"1"``    -> require native; raise if it isn't importable (CI parity lane).
- ``"auto"`` / unset -> use native iff it's importable AND the component's kernel
  symbol(s) exist -- pure-Python is a lossy fallback only when the wheel is absent
  (per the Rust-is-the-reference authority model).

The kernel is reachable two ways, tried in order:
  1. ``goldencheck._native`` -- the in-tree build dropped by
     ``scripts/build_goldencheck_native.py`` for local dev / the CI parity lane.
  2. ``goldencheck_native._native`` -- the separately-distributed
     ``goldencheck-native`` abi3 wheel (``pip install goldencheck[native]``).
     Same ``_native`` pymodule either way.
"""
from __future__ import annotations

import os
from typing import Any

try:
    import goldencheck._native as _native  # pyright: ignore[reportMissingImports]
except Exception:  # noqa: BLE001 - any import/load failure falls back below
    try:
        from goldencheck_native import _native  # pyright: ignore[reportMissingImports]
    except Exception:  # noqa: BLE001 - neither path available -> pure Python
        _native = None


def native_module() -> Any:
    """The imported ``goldencheck._native`` module (typed ``Any`` -- its kernels
    are dynamically loaded), or ``None`` if unavailable. Call sites must guard
    with ``native_enabled(...)`` first."""
    return _native


def native_available() -> bool:
    return _native is not None


def native_enabled(component: str) -> bool:
    """Whether to use the native kernel for ``component`` on this call.

    A component is also required to be *present* as an attribute on the loaded
    module -- an explicit capability probe rather than a silent ``AttributeError``
    fallback at the call site, so a stale wheel missing a newer symbol cleanly
    declines instead of crashing mid-call (the goldenmatch #688 footgun)."""
    mode = os.environ.get("GOLDENCHECK_NATIVE", "auto").lower()
    if mode == "0":
        return False
    if mode == "1":
        if _native is None:
            raise RuntimeError(
                "GOLDENCHECK_NATIVE=1 but goldencheck._native is not built/importable"
            )
        return _has_symbol(component)
    return _native is not None and _has_symbol(component)


# Component name -> the native symbol(s) that back it. A component is usable only
# when ALL its symbols are present on the loaded module (explicit capability probe,
# not a silent AttributeError mid-call -- the goldenmatch #688 footgun).
_COMPONENT_SYMBOLS: dict[str, tuple[str, ...]] = {
    "benford": ("benford_leading_digits",),
    "composite_keys": ("composite_key_search",),
    "functional_dependencies": ("discover_functional_dependencies",),
    "fuzzy_values": ("near_duplicate_value_clusters",),
    "approximate_fd": ("discover_approximate_fds", "fd_violation_rows"),
}


def _has_symbol(component: str) -> bool:
    if _native is None:
        return False
    symbols = _COMPONENT_SYMBOLS.get(component)
    if not symbols:
        return False
    return all(hasattr(_native, s) for s in symbols)
