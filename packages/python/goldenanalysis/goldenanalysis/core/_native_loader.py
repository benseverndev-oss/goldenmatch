"""Loader + gate for the optional ``goldenanalysis._native`` accelerator.

There is no native kernel yet (Phase 4 ships ``analysis-core`` / ``analysis-native``).
This module exists from day one so the gate contract is under test and the Phase 4
call sites have one place to read. Mirrors ``goldencheck.core._native_loader``.

``GOLDENANALYSIS_NATIVE`` env:
- ``"0"``    -> force the Python path (never use native).
- ``"1"``    -> require native; raise if it isn't importable (CI parity lane).
- ``"auto"`` / unset -> use native iff importable AND the component has cleared
  parity (is in ``_GATED_ON``). ``_GATED_ON`` is empty until Phase 4, so ``auto``
  always uses the pure path today.

The kernel would be reachable two ways, tried in order:
  1. ``goldenanalysis._native``        -- the in-tree build (local dev / parity lane).
  2. ``goldenanalysis_native._native`` -- the ``goldenanalysis-native`` abi3 wheel.
"""

from __future__ import annotations

import os
from typing import Any

try:
    import goldenanalysis._native as _native  # pyright: ignore[reportMissingImports]
except Exception:  # noqa: BLE001 - any import/load failure falls back below
    try:
        from goldenanalysis_native import _native  # pyright: ignore[reportMissingImports]
    except Exception:  # noqa: BLE001 - neither path available -> pure Python
        _native = None


# Components whose native path has cleared parity and may run under
# ``GOLDENANALYSIS_NATIVE=auto``. ``histogram`` / ``quantile`` joined after
# ``test_native_parity.py`` proved byte-identical output AND the wall was measured to
# move on the target env: 5.8-9.9x faster than the pure Python loop on Linux x86_64
# at 1M-10M rows, INCLUDING the list->Arrow conversion the dispatch pays (the
# ``bench-analysis-native.yml`` A/B harness). A new primitive joins only after the
# same two gates clear (heed the goldenmatch-native footguns in the root CLAUDE.md).
_GATED_ON: frozenset[str] = frozenset({"histogram", "quantile"})


def native_module() -> Any:
    """The imported ``goldenanalysis._native`` module, or ``None`` if unavailable."""
    return _native


def native_available() -> bool:
    return _native is not None


def native_enabled(component: str) -> bool:
    """Whether to use the native kernel for ``component`` on this call.

    Always ``False`` today (``_GATED_ON`` is empty). With ``GOLDENANALYSIS_NATIVE=1``
    and no built kernel, raises — the require-native CI parity contract.
    """
    mode = os.environ.get("GOLDENANALYSIS_NATIVE", "auto").lower()
    if mode == "0":
        return False
    if mode == "1":
        if _native is None:
            raise RuntimeError(
                "GOLDENANALYSIS_NATIVE=1 but goldenanalysis._native is not built/importable"
            )
        return component in _GATED_ON
    return _native is not None and component in _GATED_ON
