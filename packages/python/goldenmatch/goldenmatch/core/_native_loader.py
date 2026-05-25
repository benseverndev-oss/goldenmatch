"""Loader + gate for the optional ``goldenmatch._native`` acceleration module.

The native extension (Rust/PyO3, built from ``packages/rust/extensions/native``)
is an *optional accelerator*: when it isn't importable, the pure-Python paths run
unchanged. Selection is centralised here so every call site reads one gate.

``GOLDENMATCH_NATIVE`` env:
- ``"0"``    -> force the Python path (never use native).
- ``"1"``    -> require native; raise if it isn't importable (CI parity lane).
- ``"auto"`` / unset -> use native iff it's importable AND the component has been
  signed off (is in ``_GATED_ON``). Empty today, so the **default is Python** for
  every component until its parity + DQbench gate passes — we ship the ext able to
  run and flip the default per phase, per the spec.

Spec: ``docs/design/2026-05-25-rust-acceleration-spec.md`` §0.3.
"""
from __future__ import annotations

import os
from typing import Any

try:
    import goldenmatch._native as _native  # pyright: ignore[reportMissingImports]
except Exception:  # noqa: BLE001 - any import/load failure falls back to Python
    _native = None


# Components whose native path has cleared parity + DQbench and may run under
# ``GOLDENMATCH_NATIVE=auto``. Add a name here only after sign-off.
_GATED_ON: frozenset[str] = frozenset()


def native_module() -> Any:
    """The imported ``goldenmatch._native`` module (typed ``Any`` — its kernels
    are dynamically loaded), or ``None`` if unavailable. Call sites must guard
    with ``native_enabled(...)`` first."""
    return _native


def native_available() -> bool:
    return _native is not None


def native_enabled(component: str) -> bool:
    """Whether to use the native kernel for ``component`` on this call."""
    mode = os.environ.get("GOLDENMATCH_NATIVE", "auto").lower()
    if mode == "0":
        return False
    if mode == "1":
        if _native is None:
            raise RuntimeError(
                "GOLDENMATCH_NATIVE=1 but goldenmatch._native is not built/importable"
            )
        return True
    return _native is not None and component in _GATED_ON
