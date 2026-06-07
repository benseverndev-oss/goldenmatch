"""Loader + gate for the optional ``goldencheck._native`` acceleration module.

The native extension (Rust/PyO3, built from
``packages/rust/extensions/goldencheck-native``) is an *optional accelerator*:
when it isn't importable, the pure-Python paths run unchanged. Selection is
centralised here so every call site reads one gate. Mirrors goldenmatch's
``goldenmatch.core._native_loader``.

``GOLDENCHECK_NATIVE`` env:
- ``"0"``    -> force the Python path (never use native).
- ``"1"``    -> require native; raise if it isn't importable (CI parity lane).
- ``"auto"`` / unset -> use native iff it's importable AND the component has
  cleared parity (is in ``_GATED_ON``).

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


# Components whose native path has cleared parity and may run under
# ``GOLDENCHECK_NATIVE=auto``. Add a name here only after a parity test proves
# the native output is identical to the pure-Python reference.
#
# Signed off:
#   - benford: leading-digit histogram for the Benford conformance check
#     (baseline/statistical.py, drift/detector.py). The Rust kernel mirrors
#     `_extract_leading_digits` value-for-value; tests/core/test_native_parity.py
#     asserts the histogram (and therefore the chi-squared p-value) is identical
#     on random + adversarial data.
_GATED_ON: frozenset[str] = frozenset({"benford"})


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
    return _native is not None and component in _GATED_ON and _has_symbol(component)


# Component name -> the native symbol that backs it. A component is only usable
# when its symbol is actually present on the loaded module.
_COMPONENT_SYMBOLS: dict[str, str] = {
    "benford": "benford_leading_digits",
}


def _has_symbol(component: str) -> bool:
    if _native is None:
        return False
    symbol = _COMPONENT_SYMBOLS.get(component)
    if symbol is None:
        return False
    return hasattr(_native, symbol)
