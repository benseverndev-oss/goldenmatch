"""Parity: the native ``histogram`` / ``quantile`` kernels must produce
byte-identical output to the pure-Python reference in
``goldenanalysis.core.aggregate``. This is the gate that lets a primitive sit in
``_native_loader._GATED_ON`` (run under ``GOLDENANALYSIS_NATIVE=auto``).

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


def _f64(values):
    import pyarrow as pa

    return pa.array(values, type=pa.float64())


def _assert_histogram_parity(values, bins) -> None:
    from goldenanalysis.core import aggregate

    native = native_module().histogram(_f64(values), bins)
    pure = aggregate.histogram(values, bins)
    assert native == pure


def _assert_quantile_parity(values, q) -> None:
    from goldenanalysis.core import aggregate

    native = native_module().quantile(_f64(values), q)
    pure = aggregate.quantile(values, q)
    assert native == pure


# ---------------------------------------------------------------------------
# Parity (native present)
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
    arr = _f64([1.5, None, 200.0, None, 9.9, None])
    non_null = [1.5, 200.0, 9.9]
    from goldenanalysis.core import aggregate

    assert native_module().histogram(arr, 10) == aggregate.histogram(non_null, 10)
    assert native_module().quantile(arr, 0.5) == aggregate.quantile(non_null, 0.5)


@native_only
def test_parity_empty_and_all_equal() -> None:
    _assert_histogram_parity([], 10)
    _assert_quantile_parity([], 0.5)
    _assert_histogram_parity([2.0, 2.0, 2.0], 4)
    _assert_quantile_parity([7.0], 0.5)


# ---------------------------------------------------------------------------
# Loader gate (no wheel needed -- these never import polars/aggregate)
# ---------------------------------------------------------------------------


def test_disabled_env_forces_python(monkeypatch: pytest.MonkeyPatch) -> None:
    """GOLDENANALYSIS_NATIVE=0 always uses the Python path, even if the ext is
    present -- so the result is unchanged whether or not native is installed."""
    monkeypatch.setenv("GOLDENANALYSIS_NATIVE", "0")
    assert native_enabled("histogram") is False
    assert native_enabled("quantile") is False


def test_auto_with_empty_gated_on_uses_python(monkeypatch: pytest.MonkeyPatch) -> None:
    """_GATED_ON is empty until a wall-verified flip, so `auto` is always pure --
    even when a wheel is importable."""
    monkeypatch.setenv("GOLDENANALYSIS_NATIVE", "auto")
    assert native_enabled("histogram") is False
    assert native_enabled("quantile") is False


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
