"""Parity: the native ``histogram`` / ``quantile`` kernels must produce
byte-identical output to the pure-Python reference (``aggregate._*_pure``). This is
the gate that let them sit in ``_native_loader._GATED_ON`` (run under
``GOLDENANALYSIS_NATIVE=auto``). The reference is the ``_*_pure`` helpers, NOT the
public ``aggregate.histogram`` / ``quantile`` -- those now DISPATCH to native when
gated, so comparing against them would be native-vs-native.

Skips cleanly when the native extension isn't built (pure-Python-only env). The
loader-gate tests at the bottom run WITHOUT the wheel (they don't import polars).
"""
from __future__ import annotations

import os
import random

import pytest
from goldenanalysis.core._native_loader import (
    native_available,
    native_enabled,
    native_module,
)

native_only = pytest.mark.skipif(
    not native_available(), reason="goldenanalysis native extension not built"
)


def _f64(values: list):
    import pyarrow as pa

    return pa.array(values, type=pa.float64())


def _assert_histogram_parity(values: list, bins: int) -> None:
    from goldenanalysis.core import aggregate

    native = native_module().histogram(_f64(values), bins)
    assert native == aggregate._histogram_pure(values, bins)


def _assert_quantile_parity(values: list, q: float) -> None:
    from goldenanalysis.core import aggregate

    native = native_module().quantile(_f64(values), q)
    assert native == aggregate._quantile_pure(values, q)


def _assert_mean_parity(values: list) -> None:
    from goldenanalysis.core import aggregate

    assert native_module().mean(_f64(values)) == aggregate._mean_pure(values)


def _assert_min_max_parity(values: list) -> None:
    from goldenanalysis.core import aggregate

    assert native_module().min(_f64(values)) == aggregate._min_pure(values)
    assert native_module().max(_f64(values)) == aggregate._max_pure(values)


# ---------------------------------------------------------------------------
# Parity (native present) -- native kernel vs the pure reference helpers
# ---------------------------------------------------------------------------


@native_only
@pytest.mark.parametrize("seed", range(6))
def test_histogram_parity_random(seed: int) -> None:
    rng = random.Random(seed)
    values = [rng.uniform(-100.0, 1000.0) for _ in range(5000)]
    for bins in (1, 5, 10, 23):
        _assert_histogram_parity(values, bins)


@native_only
@pytest.mark.parametrize("seed", range(6))
def test_quantile_parity_random(seed: int) -> None:
    rng = random.Random(seed)
    values = [rng.uniform(-100.0, 1000.0) for _ in range(5000)]
    for q in (0.0, 0.05, 0.5, 0.95, 0.99, 1.0):
        _assert_quantile_parity(values, q)


@native_only
def test_parity_adversarial_magnitudes() -> None:
    # Finite edge cases most likely to diverge between Rust and Python float math:
    # exact powers of ten, tiny/huge magnitudes, negatives. (NaN/inf are out of
    # the contract -- the pure reference's min/max/sorted are undefined on them.)
    values = (
        [10.0**k for k in range(-12, 13)]
        + [9.999999999, 1.0000001, 99.9, 100.0, 999999.0, -1.0, -1e6, 5e-1, 4.4]
        + [1e-300, 1e300]
    )
    for bins in (1, 7, 10, 50):
        _assert_histogram_parity(values, bins)
    for q in (0.0, 0.25, 0.5, 0.75, 1.0):
        _assert_quantile_parity(values, q)


@native_only
def test_parity_drops_nulls() -> None:
    # Null slots must be dropped (their backing f64 is undefined), matching the
    # pure path which only sees the non-null values.
    from goldenanalysis.core import aggregate

    arr = _f64([1.5, None, 200.0, None, 9.9, None])
    non_null = [1.5, 200.0, 9.9]
    assert native_module().histogram(arr, 10) == aggregate._histogram_pure(non_null, 10)
    assert native_module().quantile(arr, 0.5) == aggregate._quantile_pure(non_null, 0.5)


@native_only
def test_parity_empty_and_all_equal() -> None:
    _assert_histogram_parity([], 10)
    _assert_quantile_parity([], 0.5)
    _assert_histogram_parity([2.0, 2.0, 2.0], 4)
    _assert_quantile_parity([7.0], 0.5)


@native_only
@pytest.mark.parametrize("seed", range(4))
def test_public_dispatch_matches_pure(monkeypatch: pytest.MonkeyPatch, seed: int) -> None:
    """The PUBLIC ``aggregate.histogram`` / ``quantile`` dispatch (native, gated, =1)
    is byte-identical to the pure path (=0) -- the end-to-end gate guarantee."""
    from goldenanalysis.core import aggregate

    rng = random.Random(seed)
    values = [rng.uniform(-100.0, 1000.0) for _ in range(5000)]

    monkeypatch.setenv("GOLDENANALYSIS_NATIVE", "1")  # force native dispatch
    native_hist = aggregate.histogram(values, 10)
    native_q = aggregate.quantile(values, 0.95)

    monkeypatch.setenv("GOLDENANALYSIS_NATIVE", "0")  # force pure
    assert native_hist == aggregate.histogram(values, 10)
    assert native_q == aggregate.quantile(values, 0.95)


# ---------------------------------------------------------------------------
# Numeric-reduction parity (mean / min / max) -- Wave 2, pure-slice kernels
# ---------------------------------------------------------------------------

_NUMERIC_FIXTURES = [
    [1.0, 2.0, 3.0],
    [5.0],
    [-3.5, -1.0, 2.25, 100.0],
    [1e16] + [1.0] * 100 + [-1e16],  # summation-order: naive native == naive pure
    [0.0, 0.0, 0.0],
]


@native_only
@pytest.mark.parametrize("xs", _NUMERIC_FIXTURES)
def test_mean_parity(xs: list) -> None:
    _assert_mean_parity(xs)


@native_only
@pytest.mark.parametrize("xs", _NUMERIC_FIXTURES)
def test_min_max_parity(xs: list) -> None:
    _assert_min_max_parity(xs)


@native_only
@pytest.mark.parametrize("seed", range(4))
def test_mean_parity_random(seed: int) -> None:
    rng = random.Random(seed)
    _assert_mean_parity([rng.uniform(-100.0, 1000.0) for _ in range(5000)])
    _assert_min_max_parity([rng.uniform(-100.0, 1000.0) for _ in range(5000)])


@native_only
def test_numeric_reductions_drop_nulls() -> None:
    # Null slots dropped, matching the pure path which only sees non-null values.
    from goldenanalysis.core import aggregate

    arr = _f64([1.5, None, 200.0, None, 9.9])
    non_null = [1.5, 200.0, 9.9]
    assert native_module().mean(arr) == aggregate._mean_pure(non_null)
    assert native_module().min(arr) == aggregate._min_pure(non_null)
    assert native_module().max(arr) == aggregate._max_pure(non_null)


# ---------------------------------------------------------------------------
# Frame-kernel parity (null_ratio / duplicate_row / distinct_count)
# ---------------------------------------------------------------------------


def _frames() -> dict:
    import polars as pl

    return {
        "empty": pl.DataFrame({"a": []}),
        "all_null": pl.DataFrame({"a": [None, None, None]}),
        "null_bearing": pl.DataFrame({"a": [1, None, 3], "b": ["x", None, "x"]}),
        "mixed_dup": pl.DataFrame(  # rows 0,1,2 identical => dup group of 3
            {
                "s": ["a", "a", "a", "b"],
                "i": [1, 1, 1, 2],
                "f": [1.5, 1.5, 1.5, 2.5],
                "b": [True, True, True, False],
            }
        ),
        "float_edge": pl.DataFrame(  # -0.0/+0.0 fold, NaN folds (polars-verified)
            {
                "f": [-0.0, 0.0, float("nan"), float("nan"), 1.0],
                "k": [1, 1, 2, 2, 3],
            }
        ),
    }


@native_only
@pytest.mark.parametrize("name", sorted(_frames()))
def test_duplicate_row_ratio_parity(name: str) -> None:
    from goldenanalysis.core import aggregate

    df = _frames()[name]
    native = native_module().duplicate_row_ratio([df[c].to_arrow() for c in df.columns])
    assert native == aggregate._duplicate_row_ratio_pure(df)


@native_only
@pytest.mark.parametrize("name", sorted(_frames()))
def test_null_ratio_per_column_parity(name: str) -> None:
    from goldenanalysis.core import aggregate

    df = _frames()[name]
    ratios = native_module().null_ratio_per_column([df[c].to_arrow() for c in df.columns])
    native = dict(zip(df.columns, ratios))
    assert native == aggregate._null_ratio_per_column_pure(df)


@native_only
@pytest.mark.parametrize("name", sorted(_frames()))
def test_distinct_count_parity(name: str) -> None:
    from goldenanalysis.core import aggregate

    df = _frames()[name]
    for col in df.columns:
        native = native_module().distinct_count(df[col].to_arrow())
        assert native == aggregate._distinct_count_pure(df[col])


def test_native_dtype_fallback_returns_pure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Box-runnable: an unsupported dtype (List) makes the native call raise, and the
    public dispatchers must swallow it and return the pure result (no build needed --
    we monkeypatch a native module whose methods raise)."""
    import polars as pl
    from goldenanalysis.core import aggregate as agg

    df = pl.DataFrame({"a": [[1, 2], [3, 4], [1, 2]]})  # List dtype -> intern rejects

    monkeypatch.setattr(agg, "native_enabled", lambda name: True)

    class _Boom:
        def null_ratio_per_column(self, cols):
            raise TypeError("unsupported dtype")

        def duplicate_row_ratio(self, cols):
            raise TypeError("unsupported dtype")

    monkeypatch.setattr(agg, "native_module", lambda: _Boom())
    assert agg.null_ratio_per_column(df) == agg._null_ratio_per_column_pure(df)
    assert agg.duplicate_row_ratio(df) == agg._duplicate_row_ratio_pure(df)


# ---------------------------------------------------------------------------
# Loader gate (no wheel needed -- these never import polars/aggregate)
# ---------------------------------------------------------------------------


def test_disabled_env_forces_python(monkeypatch: pytest.MonkeyPatch) -> None:
    """GOLDENANALYSIS_NATIVE=0 always uses the Python path, even if the ext is
    present -- so the result is unchanged whether or not native is installed."""
    monkeypatch.setenv("GOLDENANALYSIS_NATIVE", "0")
    assert native_enabled("histogram") is False
    assert native_enabled("quantile") is False


def test_auto_gates_histogram_and_quantile(monkeypatch: pytest.MonkeyPatch) -> None:
    """histogram/quantile are gated, so under `auto` they use native iff a wheel is
    importable. A non-gated component always stays pure."""
    monkeypatch.setenv("GOLDENANALYSIS_NATIVE", "auto")
    assert native_enabled("histogram") is native_available()
    assert native_enabled("quantile") is native_available()
    assert native_enabled("frame.row_count") is False  # not in _GATED_ON


@pytest.mark.skipif(native_available(), reason="wheel present -> =1 does not raise")
def test_required_mode_without_wheel_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """GOLDENANALYSIS_NATIVE=1 with no built kernel is the require-native CI
    contract: it raises rather than silently falling back."""
    monkeypatch.setenv("GOLDENANALYSIS_NATIVE", "1")
    with pytest.raises(RuntimeError):
        native_enabled("histogram")


def test_required_mode_implies_wheel_when_set() -> None:
    """In the CI native lane (GOLDENANALYSIS_NATIVE=1) the wheel MUST be importable
    -- otherwise every native_only test would silently skip."""
    if os.environ.get("GOLDENANALYSIS_NATIVE") == "1":
        assert native_available()
