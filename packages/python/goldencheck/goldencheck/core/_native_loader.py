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
#   - composite_keys: minimal composite-key discovery
#     (relations/composite_key.py). Integer-exact (distinct-tuple counting), so
#     native and the pure-Python BFS return identical minimal-key sets; parity
#     asserted in tests/core/test_native_parity.py.
#   - functional_dependencies: A->B determinism primitive. Integer-exact; parity
#     asserted in the same test module.
#   - fuzzy_values: near-duplicate value clustering (trigram+prefix blocking +
#     Levenshtein-ratio). The pure-Python fallback uses the identical metric +
#     blocking, so cluster sets match; parity asserted in the same test module.
#   - approximate_fd: near-FD violation detection (find the per-determinant-group
#     mode dependent, flag deviating rows). The pure-Python fallback uses the
#     identical first-seen interning + mode tie-break + avg-group guard, so the
#     violation sets match; parity asserted in the same test module.
_GATED_ON: frozenset[str] = frozenset(
    {"benford", "composite_keys", "functional_dependencies", "fuzzy_values", "approximate_fd"}
)


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
    # Reference mode (docs/design/2026-07-01-rust-is-the-reference-roadmap.md): native
    # runs wherever the component's kernel symbol exists; ``_GATED_ON`` is retained as
    # byte-exact sign-off documentation but no longer governs ``auto``.
    return _native is not None and _has_symbol(component)


# Component name -> the native symbol(s) that back it. A component is only usable
# when ALL its symbols are present on the loaded module. Most components need one
# symbol; ``approximate_fd`` needs two -- its call site (``relations/approx_fd.py``)
# calls BOTH ``discover_approximate_fds`` and ``fd_violation_rows``, so a stale
# wheel carrying only the former would pass a single-symbol probe, run the first
# call, then ``AttributeError`` on the second and fall fully back to Python -- a
# silent redundant native pass (the goldenmatch #688 footgun). Requiring every
# symbol makes such a wheel cleanly decline up front.
_COMPONENT_SYMBOLS: dict[str, tuple[str, ...]] = {
    "benford": ("benford_leading_digits",),
    "composite_keys": ("composite_key_search",),
    "functional_dependencies": ("discover_functional_dependencies",),
    "fuzzy_values": ("near_duplicate_value_clusters",),
    "approximate_fd": ("discover_approximate_fds", "fd_violation_rows"),
    "denial_constraint": ("denial_constraint_evidence",),
}


def _has_symbol(component: str) -> bool:
    if _native is None:
        return False
    symbols = _COMPONENT_SYMBOLS.get(component)
    if not symbols:
        return False
    return all(hasattr(_native, s) for s in symbols)
