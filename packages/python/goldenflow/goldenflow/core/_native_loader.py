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
# Signed off 2026-06-07 (NANP-only gating):
#   - phone: the phone kernel runs in ``nanp_only`` mode (the Python bridge
#     passes it), so it emits a result ONLY for NANP numbers (country calling
#     code 1) and null for everything else. Characterization across 20 country
#     metadata sets showed the Rust ``phonenumber`` port is byte-identical to
#     the Python ``phonenumbers`` library EXCEPT when a ``+CC`` international
#     number is parsed with a mismatched default region ("US") and its national
#     number starts with "1" (e.g. ``+33142685300`` -> native ``+3342685300``):
#     the port mis-applies US national-prefix stripping. Those diverging
#     outputs are never country-code-1, so restricting native to NANP results
#     sidesteps the bug entirely; international rows fall back to the Python
#     reference. Parity asserted over a NANP residual corpus (alpha, extensions,
#     ambiguous leading-1, odd formats) AND a mixed intl corpus in
#     tests/transforms/test_native_parity.py.
_GATED_ON: frozenset[str] = frozenset({"phone"})


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
