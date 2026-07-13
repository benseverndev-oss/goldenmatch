"""Owned auto-detect profile kernel wiring tests (Phase 3).

Two lanes, following the ``test_native_parity.py`` convention:

- Pure-path contract guards (always run): assert the ``inferred_type`` decision
  and raw ``unique_count`` invariants hold on whichever path executes. They pass
  on the pure path today (native symbol absent) -- that is intentional.
- Native-lane equivalence tests (name contains ``native``): the fallback lane
  ``-k "not native"`` deselects them; they SKIP when the native kernel / symbol
  is absent, and only genuinely exercise the kernel in CI's native lane.
"""
from __future__ import annotations

import pytest
from goldenflow.core._native_loader import native_available, native_module
from goldenflow.engine.profiler_bridge import _infer_type_list, profile_columns

# A battery of columns spanning every branch of the decision.
_BATTERY: dict[str, list] = {
    "email": ["a@b.co", "x@y.io", "p@q.net"],
    "zip": ["12345", "90210", "10001-1234"],
    "date": ["2020-01-02", "1999/12/31", "1/2/99"],
    "phone": ["(212) 555-1234", "+1 415 555 9999", "212-555-0000"],
    "name": ["John Smith", "Jane Doe", "Bob Roe"],
    "nums": [1, 2, 3],
    "floats": [1.5, 2.5, 3.5],
    "bools": [True, False, True],
    "mixed": [1, "1"],
    "strings": ["foo", "bar", "baz"],
    "with_nulls": ["a@b.co", None, "x@y.io", "p@q.net"],
    "all_null": [None, None],
    "blanks": ["   ", ""],
}


def test_profile_columns_inferred_type_matches_pure():
    cols = {
        "email": ["a@b.co", "x@y.io", "p@q.net"],
        "zip": ["12345", "90210", "10001-1234"],
        "nums": [1, 2, 3],
        "mixed": [1, "1"],  # -> string; unique_count must be 2 (raw)
        "name": ["John Smith", "Jane Doe", "Bob Roe"],
    }
    prof = profile_columns(cols)
    got = {c.name: c.inferred_type for c in prof.columns}
    assert got == {
        "email": "email",
        "zip": "zip",
        "nums": "numeric",
        "mixed": "string",
        "name": "name",
    }
    mixed = next(c for c in prof.columns if c.name == "mixed")
    assert mixed.unique_count == 2  # raw-value set, NOT stringified


def _native_profile_or_skip():
    if not native_available():
        pytest.skip("goldenflow-native not built/importable")
    nm = native_module()
    if nm is None or not hasattr(nm, "infer_type_list_arrow"):
        pytest.skip("installed goldenflow-native predates the profile kernel")
    return nm


def _hint_for(values: list) -> str:
    """Derive the TypeHint string exactly as ``_infer_type_list`` decides."""
    non_null = [v for v in values if v is not None]
    if non_null and all(isinstance(v, bool) for v in non_null):
        return "boolean"
    if non_null and all(
        isinstance(v, (int, float)) and not isinstance(v, bool) for v in non_null
    ):
        return "numeric"
    return "string"


def test_native_infer_type_list_equals_pure_native(monkeypatch):
    nm = _native_profile_or_skip()
    monkeypatch.setenv("GOLDENFLOW_NATIVE", "auto")
    for name, values in _BATTERY.items():
        hint = _hint_for(values)
        strs = [None if v is None else str(v) for v in values]
        native_out = nm.infer_type_list_arrow(strs, hint)
        pure_out = _infer_type_list(values)
        assert native_out == pure_out, f"{name}: native={native_out!r} pure={pure_out!r}"
