"""Loader + gate for the ``goldengraph._native`` knowledge-graph engine.

GoldenGraph is **native-authoritative** (docs/design/2026-07-01-rust-is-the-
reference-roadmap.md): the pyo3-free ``goldengraph-core`` crate is the reference
implementation of build_graph / neighborhood / seeds_by_name / communities and
the bi-temporal store, and it is the SAME kernel the TS/WASM (``goldengraph-
wasm``) and C-ABI (``goldengraph-cabi``) surfaces run -- so every surface is
byte-identical by construction over one shared JSON boundary. Unlike the profiling
packages there is no pure-Python fallback for these primitives (the store /
resolution engine is Rust-only); the gate exists to give call sites ONE place to
read the engine and to make the require-native contract explicit and testable.

``GOLDENGRAPH_NATIVE`` env:
- ``"0"``    -> force-disable (``native_enabled`` returns False; callers that have
  no fallback will raise a clear error rather than silently degrade).
- ``"1"``    -> require native; raise if it is not importable (the CI parity lane).
- ``"auto"`` / unset -> use native wherever the component's kernel symbol exists
  (``_COMPONENT_SYMBOLS`` / ``_has_symbol``).

The engine is reachable two ways, tried in order:
  1. ``goldengraph._native``        -- the in-tree build (local dev / parity lane).
  2. ``goldengraph_native._native`` -- the ``goldengraph-native`` abi3 wheel.

This module is the ONE gated entry point for the whole native engine, not just
the JSON parity surface: ``new_store()`` builds the bi-temporal ``PyStore`` and
``profile_resolve_json()`` reaches the sibling ``goldenprofile-native``
resolution sub-engine used by ``goldengraph.profile``. Both honor the same
``GOLDENGRAPH_NATIVE`` contract, so ``=0`` force-disables the whole engine (with
a clear error, since there is no pure-Python fallback) rather than only the
parity surface.
"""

from __future__ import annotations

import os
from typing import Any

try:
    import goldengraph._native as _native  # pyright: ignore[reportMissingImports]
except Exception:  # noqa: BLE001 - any import/load failure falls back below
    try:
        from goldengraph_native import _native  # pyright: ignore[reportMissingImports]
    except Exception:  # noqa: BLE001 - neither path available
        _native = None


# Component -> the native symbol that backs it. These are the JSON-boundary
# functions shared with goldengraph-wasm / -cabi (``(json, args...) -> json``),
# the parity-checkable surface asserted by ``tests/test_native_parity.py``
# against the shared ``queries.json`` oracle.
_COMPONENT_SYMBOLS: dict[str, str] = {
    "build_graph": "build_graph_json",
    "neighborhood": "neighborhood_json",
    "seeds_by_name": "seeds_by_name_json",
    "communities": "communities_json",
    "store_append": "store_append_json",
    "store_as_of": "store_as_of_json",
    "store_history": "store_history_json",
}


def _has_symbol(component: str) -> bool:
    if _native is None:
        return False
    symbol = _COMPONENT_SYMBOLS.get(component)
    return symbol is not None and hasattr(_native, symbol)


def native_module() -> Any:
    """The imported ``goldengraph._native`` module, or ``None`` if unavailable."""
    return _native


def native_available() -> bool:
    return _native is not None


def native_enabled(component: str) -> bool:
    """Whether to use the native kernel for ``component`` on this call.

    Reference mode: native runs wherever the component's kernel symbol exists.
    With ``GOLDENGRAPH_NATIVE=1`` and no built kernel, raises -- the require-native
    CI parity contract.
    """
    mode = os.environ.get("GOLDENGRAPH_NATIVE", "auto").lower()
    if mode == "0":
        return False
    if mode == "1":
        if _native is None:
            raise RuntimeError(
                "GOLDENGRAPH_NATIVE=1 but goldengraph._native is not built/importable"
            )
        return _has_symbol(component)
    return _native is not None and _has_symbol(component)


def new_store() -> Any:
    """Construct a fresh bi-temporal ``PyStore`` through the gated engine.

    The single entry point callers use instead of importing the wheel directly,
    so the ``GOLDENGRAPH_NATIVE`` contract governs the store too: ``=0``
    force-disables (raise -- there is no pure-Python store fallback), and a
    missing/unbuilt engine raises a clear, actionable error rather than an opaque
    ``ImportError`` at the call site.
    """
    if os.environ.get("GOLDENGRAPH_NATIVE", "auto").lower() == "0":
        raise RuntimeError(
            "GOLDENGRAPH_NATIVE=0 force-disables the native engine, but goldengraph "
            "has no pure-Python store fallback -- the bi-temporal store is Rust-only."
        )
    engine = native_module()
    if engine is None or not hasattr(engine, "PyStore"):
        raise RuntimeError(
            "goldengraph native engine is not built/importable (needed for the "
            "bi-temporal PyStore). Build it with scripts/build_goldengraph_native.py "
            "or `pip install goldengraph-native`."
        )
    return engine.PyStore()


def profile_resolve_json() -> Any:
    """The gated ``goldenprofile-native`` ``resolve_json`` entry point.

    ``goldengraph.profile`` resolves Virtual Fingerprints through the separate
    ``goldenprofile-native`` wheel (its own pyo3 binding over a pyo3-free Rust
    core). Routing it here keeps ONE gated entry point for the engine: the same
    ``GOLDENGRAPH_NATIVE`` contract applies (``=0`` force-disables), and the
    wheel is imported lazily so importing this module never requires it.
    """
    if os.environ.get("GOLDENGRAPH_NATIVE", "auto").lower() == "0":
        raise RuntimeError(
            "GOLDENGRAPH_NATIVE=0 force-disables the native engine, but goldengraph "
            "has no pure-Python fingerprint-resolution fallback."
        )
    try:
        from goldenprofile_native import resolve_json  # pyright: ignore[reportMissingImports]
    except ImportError as e:  # pragma: no cover - exercised only without the wheel
        raise ImportError(
            "goldenprofile_native is required to resolve profiles. Build it with "
            "`maturin develop` in packages/rust/extensions/goldenprofile-native, "
            "or `pip install goldenprofile-native`."
        ) from e
    return resolve_json
