"""Loader + gate for the optional ``goldenflow._native`` acceleration module.

Mirrors ``goldenmatch.core._native_loader``. The native extension (Rust/PyO3,
built from ``packages/rust/extensions/native-flow``) is an *optional
accelerator*: when it isn't importable — or a component hasn't cleared the
parity gate — the pure-Python transform paths run unchanged.

``GOLDENFLOW_NATIVE`` env:
- ``"0"``    -> force the Python path (never use native).
- ``"1"``    -> require native; raise if it isn't importable. This is the
  parity/opt-in lane: it enables native for ALL components regardless of
  ``_GATED_ON``, so it WILL change outputs where native diverges from the
  Python reference (see below). Use only when you accept that.
- ``"auto"`` / unset -> use native iff it's importable AND the component is in
  ``_GATED_ON`` (signed off on parity). Default.

The kernel is reachable two ways, tried in order, exactly like goldenmatch:
  1. ``goldenflow._native``        — in-tree build (scripts/build_native.py).
  2. ``goldenflow_native._native`` — the separately-distributed
     ``goldenflow-native`` abi3 wheel (``pip install goldenflow[native]``).
"""
from __future__ import annotations

import os
from typing import Any

try:
    import goldenflow._native as _native  # type: ignore[import-not-found]
except Exception:  # noqa: BLE001 - any import/load failure falls back below
    try:
        from goldenflow_native import _native  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001 - neither path available -> pure Python
        _native = None


# Components whose native path has cleared parity and may run under
# ``GOLDENFLOW_NATIVE=auto``. Add a name here ONLY after a parity sign-off.
#
# Currently EMPTY by design. The phone kernel is built and wired, but measured
# parity against the installed ``phonenumbers`` library is NOT byte-identical:
# the Rust ``phonenumber`` port formats some international national numbers
# differently (e.g. "+33 1 42 68 53 00" -> native "+3342685300" vs python
# "+33142685310"-style "+33142685300"; it drops the national leading digit).
# Until that is reconciled (metadata-version alignment, or restricting native
# acceptance to a proven parity-safe subset), ``phone`` stays out of _GATED_ON
# so ``auto`` never silently changes a cleaned value. ``GOLDENFLOW_NATIVE=1``
# still exercises it for benchmarking / the parity lane.
_GATED_ON: frozenset[str] = frozenset()


def native_module() -> Any:
    """The imported native module, or ``None`` if unavailable. Guard call sites
    with ``native_enabled(...)`` first."""
    return _native


def native_available() -> bool:
    return _native is not None


def native_enabled(component: str) -> bool:
    """Whether to use the native kernel for ``component`` on this call."""
    mode = os.environ.get("GOLDENFLOW_NATIVE", "auto").lower()
    if mode == "0":
        return False
    if mode == "1":
        if _native is None:
            raise RuntimeError(
                "GOLDENFLOW_NATIVE=1 but goldenflow._native is not built/importable"
            )
        return True
    return _native is not None and component in _GATED_ON
