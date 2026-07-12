"""Phase-2 fused numeric-digest parity gate.

The fused ``column_numeric_stats`` kernel now folds ``n_unique``
(distinct-count) into the SAME streaming pass that computes count/min/max/mean/
std/sum, so a numeric column's cardinality is a free byproduct of the stats scan
instead of a separate ``pc.count_distinct`` pass. This asserts the fused
n_unique is byte-identical to pyarrow ``count_distinct(mode="all")`` -- the
current authority -- across the tricky float/int/null cases, on BOTH the native
kernel path and the pyarrow fallback path.

Target semantics (measured from pyarrow, do NOT deviate):
- All NaN collapse to ONE distinct (``[1.0, nan, nan, 2.0]`` -> 3).
- ``+0.0`` and ``-0.0`` are DISTINCT (``[0.0, -0.0, 1.0]`` -> 3).
- A null slot counts as one distinct under mode="all"
  (``[1,2,2,None,3]`` -> 4; ``[nan, None, nan, 1.0]`` -> 3).
"""
from __future__ import annotations

import os

import pytest

pa = pytest.importorskip("pyarrow")
pc = pytest.importorskip("pyarrow.compute")

from goldencheck.core._native_loader import native_enabled  # noqa: E402
from goldencheck.core.frame import ArrowColumn  # noqa: E402

_NAN = float("nan")

# (name, pyarrow array). Each is checked for n_unique parity vs count_distinct.
_CASES: list[tuple[str, pa.Array]] = [
    ("nan_collapse", pa.array([1.0, _NAN, _NAN, 2.0], type=pa.float64())),
    ("signed_zero_distinct", pa.array([0.0, -0.0, 1.0], type=pa.float64())),
    ("nan_and_null", pa.array([_NAN, None, _NAN, 1.0], type=pa.float64())),
    ("int_with_null", pa.array([1, 2, 2, None, 3], type=pa.int64())),
    ("all_same_int", pa.array([7, 7, 7, 7], type=pa.int64())),
    ("all_same_float", pa.array([3.5, 3.5, 3.5], type=pa.float64())),
    ("all_null_int", pa.array([None, None, None], type=pa.int64())),
    ("all_null_float", pa.array([None, None], type=pa.float64())),
    ("empty_int", pa.array([], type=pa.int64())),
    ("empty_float", pa.array([], type=pa.float64())),
    ("uint_with_null", pa.array([10, 20, 20, None], type=pa.uint32())),
    ("int32", pa.array([-1, -1, 0, 5, 5], type=pa.int32())),
    ("float32_nan", pa.array([1.0, _NAN, _NAN, 2.0, -0.0, 0.0], type=pa.float32())),
    ("neg_and_pos", pa.array([-100, 100, -100, 100, 0], type=pa.int64())),
]


def _large_cases() -> list[tuple[str, pa.Array]]:
    import random

    rng = random.Random(20260712)
    floats = [rng.choice([rng.random(), _NAN, 0.0, -0.0, 1.0]) for _ in range(50_000)]
    ints = [rng.randint(0, 5_000) for _ in range(50_000)]
    ints_nullable = [None if rng.random() < 0.1 else rng.randint(0, 500) for _ in range(50_000)]
    return [
        ("large_random_float", pa.array(floats, type=pa.float64())),
        ("large_random_int", pa.array(ints, type=pa.int64())),
        ("large_nullable_int", pa.array(ints_nullable, type=pa.int64())),
    ]


ALL_CASES = _CASES + _large_cases()


def _expected(arr: pa.Array) -> int:
    return int(pc.count_distinct(arr, mode="all").as_py())


@pytest.mark.parametrize("name,arr", ALL_CASES, ids=[c[0] for c in ALL_CASES])
def test_arrowcolumn_n_unique_matches_count_distinct(name, arr):
    """ArrowColumn.n_unique() (numeric path -> fused stats tuple) == pyarrow
    count_distinct(mode="all"), on whichever native/fallback path is active."""
    col = ArrowColumn(arr)
    assert col.n_unique() == _expected(arr), name


@pytest.mark.parametrize("name,arr", ALL_CASES, ids=[c[0] for c in ALL_CASES])
def test_arrowcolumn_n_unique_fallback_matches(name, arr, monkeypatch):
    """Force the pyarrow fallback (GOLDENCHECK_NATIVE=0) and re-verify parity, so
    both the native kernel and the _numeric_stats_pyarrow tuple agree with
    count_distinct."""
    monkeypatch.setenv("GOLDENCHECK_NATIVE", "0")
    assert not native_enabled("numeric_stats")
    col = ArrowColumn(arr)
    assert col.n_unique() == _expected(arr), name


@pytest.mark.skipif(
    not native_enabled("numeric_stats"),
    reason="goldencheck._native.column_numeric_stats not built/enabled",
)
@pytest.mark.parametrize("name,arr", ALL_CASES, ids=[c[0] for c in ALL_CASES])
def test_native_kernel_tuple_n_unique(name, arr):
    """The native kernel's 7-tuple field [6] (n_unique) == count_distinct for the
    non-empty/non-all-null columns it actually computes (empty/all-null are
    guarded to None in _numeric_stats and sourced via count_distinct separately;
    ArrowColumn.n_unique still matches -- covered above)."""
    col = ArrowColumn(arr)
    s = col._numeric_stats()
    if s is None:
        # empty / all-null: guarded before the kernel; n_unique() path handles it.
        assert col.n_unique() == _expected(arr), name
    else:
        assert s[6] == _expected(arr), name


def test_env_default_uses_native_when_built():
    """Sanity: with the in-tree build present and GOLDENCHECK_NATIVE unset, the
    numeric_stats gate is on (documents which path the first parametrized test
    exercised)."""
    os.environ.pop("GOLDENCHECK_NATIVE", None)
    # Not an assertion on availability (CI may not build native), just a smoke
    # that the gate query does not raise.
    native_enabled("numeric_stats")
