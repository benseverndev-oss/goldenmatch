"""Loader + gate for the optional ``goldenpipe._native`` binding.

Mirrors ``goldenflow.core._native_loader``. The binding (Rust/PyO3, built from
``packages/rust/extensions/goldenpipe-native``) exposes ``goldenpipe-core`` — the
REFERENCE planner kernel — to Python. It is NOT a runtime accelerator: the
pure-Python planner stays the runtime; this loader exists so the parity gate can
reach the kernel (and so a future reference-mode flip has the seam ready).

``GOLDENPIPE_NATIVE`` env:
- ``"0"``   -> force pure (never use native).
- ``"1"``   -> require native; raise if not importable (the CI parity lane).
- ``"auto"`` / unset -> native available iff the floor symbol exists. Default.

Reachable two ways, tried in order (like goldenflow/goldenmatch):
  1. ``goldenpipe._native``        — in-tree build (scripts/build_native.py).
  2. ``goldenpipe_native._native`` — the separate ``goldenpipe-native`` abi3 wheel.
"""
from __future__ import annotations

import os
from typing import Any

try:
    import goldenpipe._native as _native  # type: ignore[import-not-found]
except Exception:  # noqa: BLE001 - any import/load failure falls back below
    try:
        from goldenpipe_native import _native  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001 - neither path available -> pure Python
        _native = None


# Floor symbols per component (wheel-skew safe: probe the actual module).
_COMPONENT_SYMBOLS: dict[str, tuple[str, ...]] = {
    "planner": ("resolve_json",),
}


def _has_symbol(component: str) -> bool:
    if _native is None:
        return False
    syms = _COMPONENT_SYMBOLS.get(component)
    if not syms:
        return False
    return any(hasattr(_native, s) for s in syms)


def native_module() -> Any:
    """The imported native module, or ``None``. Guard with ``native_enabled`` first."""
    return _native


def native_available() -> bool:
    return _native is not None


def native_enabled(component: str) -> bool:
    mode = os.environ.get("GOLDENPIPE_NATIVE", "auto").lower()
    if mode == "0":
        return False
    if mode == "1":
        if _native is None:
            raise RuntimeError(
                "GOLDENPIPE_NATIVE=1 but goldenpipe._native is not built/importable"
            )
        return True
    return _native is not None and _has_symbol(component)


# Thin pass-throughs the parity test's Leg B calls (guard with native_enabled first).
def resolve_json(input: str) -> str:
    return _native.resolve_json(input)


def apply_decision_json(input: str) -> str:
    return _native.apply_decision_json(input)


def evaluate_builtin_json(input: str) -> str:
    return _native.evaluate_builtin_json(input)


def auto_config_json(input: str) -> str:
    return _native.auto_config_json(input)


def skip_if_falsy_json(input: str) -> str:
    return _native.skip_if_falsy_json(input)


def plan_pipeline_json(input: str) -> str:
    return _native.plan_pipeline_json(input)


def apply_scale_hints_json(input: str) -> str:
    return _native.apply_scale_hints_json(input)


def band_of_json(input: str) -> str:
    return _native.band_of_json(input)


def build_repair_plan_json(input: str) -> str:
    return _native.build_repair_plan_json(input)


def lower_json(input: str) -> str:
    return _native.lower_json(input)


def provenance_json(input: str) -> str:
    return _native.provenance_json(input)
