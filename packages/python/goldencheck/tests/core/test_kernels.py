"""Parity tests for the list-shaped kernel API (``goldencheck.core.kernels``).

This module is the single source of truth shared by the in-process API, the
DuckDB ``goldencheck_*`` UDFs and the Postgres pgrx surface. The native
``goldencheck-core`` kernel and the pure-Python fallback must return identical
results for every function -- that byte-identity is what makes the SQL surfaces
trustworthy.
"""
from __future__ import annotations

import importlib

import pytest


def _reload_with_native(mode: str):
    import os

    os.environ["GOLDENCHECK_NATIVE"] = mode
    import goldencheck.core._native_loader as nl
    import goldencheck.core.kernels as K

    importlib.reload(nl)
    importlib.reload(K)
    return K, nl


# Shared fixtures exercised on both paths.
_BENFORD = [1, 1, 2, 11, 19, 3, 100, 250, 2000, 7, 8, 9, 9, 9, 0, -5]
_FUZZY = ["California", "Californa", "CALIFORNIA", "Texas", "texas", "NewYork"]
_COLS = [
    ["1", "1", "2", "2", "3", "3"],   # zip
    ["A", "A", "B", "B", "C", "C"],   # city (zip -> city strict)
    ["S", "S", "S", "T", "T", "T"],   # state (approx related)
    ["r0", "r1", "r2", "r3", "r4", "r5"],  # unique id
    ["k", "k", "k", "k", "k", "k"],   # constant
]
_COMPOSITE = [
    ["o1", "o1", "o2", "o2", "o3"],
    ["1", "2", "1", "2", "1"],
    ["mon", "mon", "tue", "tue", "wed"],
]


def _snapshot(K):
    return {
        "benford": K.benford_histogram(_BENFORD),
        "fuzzy": K.near_duplicate_clusters(_FUZZY, 0.7),
        "fds": K.discover_functional_dependencies(_COLS),
        "approx": K.discover_approximate_fds(_COLS, 0.5),
        "composite": K.composite_key_search(_COMPOSITE, 3),
    }


def test_native_matches_python_fallback():
    """Every kernel must return identical results native vs pure-Python."""
    native_available = False
    try:
        import goldencheck._native  # noqa: F401

        native_available = True
    except Exception:  # noqa: BLE001
        try:
            from goldencheck_native import _native  # noqa: F401

            native_available = True
        except Exception:  # noqa: BLE001
            native_available = False

    py_K, _ = _reload_with_native("0")
    py = _snapshot(py_K)

    if not native_available:
        pytest.skip("native goldencheck kernel not built; fallback path only")

    nat_K, _ = _reload_with_native("1")
    nat = _snapshot(nat_K)

    # Restore the default gate for the rest of the session.
    _reload_with_native("auto")

    assert nat == py, f"native != fallback\nnative={nat}\npython={py}"


def test_fallback_pinned_values():
    """Guard the pure-Python reference against silent drift."""
    K, _ = _reload_with_native("0")
    assert K.benford_histogram([1, 1, 2, 11, 19, 3, 100, 7, 9, 9]) == [
        5, 1, 1, 0, 0, 0, 1, 0, 2,
    ]
    assert K.near_duplicate_clusters(
        ["California", "Californa", "CALIFORNIA", "Texas", "texas"], 0.7
    ) == [[0, 1, 2], [3, 4]]
    assert [0, 1] in K.composite_key_search(_COMPOSITE, 3)
    _reload_with_native("auto")


def test_degenerate_inputs():
    K, _ = _reload_with_native("0")
    assert K.benford_histogram([]) == [0] * 9
    assert K.near_duplicate_clusters([], 0.8) == []
    assert K.discover_functional_dependencies([["a", "b"]]) == []  # <2 columns
    assert K.discover_approximate_fds([["a", "b"]], 0.9) == []
    assert K.composite_key_search([["a", "b"]], 3) == []  # <2 columns
    _reload_with_native("auto")
