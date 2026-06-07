"""Parity: the native kernels must produce byte-identical output to the
pure-Python reference. This is the gate that lets a component sit in
``_native_loader._GATED_ON`` (run under ``GOLDENCHECK_NATIVE=auto``).

Skips cleanly when the native extension isn't built (pure-Python-only env)."""
from __future__ import annotations

import random
from collections import Counter

import numpy as np
import pytest
from goldencheck.baseline import statistical as st
from goldencheck.core._native_loader import native_available, native_module

native_only = pytest.mark.skipif(
    not native_available(), reason="goldencheck native extension not built"
)


def _python_histogram(values: np.ndarray) -> list[int]:
    """The pure-Python leading-digit histogram (digits 1..9)."""
    counts = Counter(st._extract_leading_digits(values))
    return [counts.get(d, 0) for d in range(1, 10)]


@native_only
@pytest.mark.parametrize("seed", range(6))
def test_benford_histogram_parity_random(seed: int) -> None:
    import pyarrow as pa

    rng = random.Random(seed)
    values = np.array(
        [rng.uniform(1e-4, 1e7) for _ in range(8000)]
        + [rng.lognormvariate(0, 4) for _ in range(2000)],
        dtype=np.float64,
    )
    native_hist = list(native_module().benford_leading_digits(pa.array(values)))
    assert native_hist == _python_histogram(values)


@native_only
def test_benford_histogram_parity_adversarial() -> None:
    """Exact powers of 10, tiny/huge magnitudes, and skipped values -- the
    float edge cases most likely to diverge between Rust and Python ``log10``."""
    import pyarrow as pa

    values = np.array(
        [10.0**k for k in range(-12, 13)]  # exact powers of 10
        + [9.999999999, 1.0000001, 99.9, 100.0, 999999.0]
        + [0.0, -1.0, -1e6, float("nan"), float("inf"), float("-inf")]
        + [1e-300, 1e300, 5e-1, 4.4],
        dtype=np.float64,
    )
    native_hist = list(native_module().benford_leading_digits(pa.array(values)))
    assert native_hist == _python_histogram(values)


@native_only
def test_benford_handles_nulls() -> None:
    """Null slots must be dropped (their backing f64 is undefined), matching
    the Python path which only sees non-null values."""
    import pyarrow as pa

    arr = pa.array([1.5, None, 200.0, None, 9.9], type=pa.float64())
    native_hist = list(native_module().benford_leading_digits(arr))
    py = _python_histogram(np.array([1.5, 200.0, 9.9], dtype=np.float64))
    assert native_hist == py


@native_only
def test_compute_benford_native_matches_python(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end: the chi-squared p-value dict is identical with the native
    kernel forced on vs forced off."""
    rng = random.Random(99)
    # Benford-ish: first digits weighted toward 1 across several magnitudes.
    values = np.array(
        [rng.choice([1, 1, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9]) * 10.0 ** rng.randint(0, 5)
         + rng.random() for _ in range(5000)],
        dtype=np.float64,
    )

    monkeypatch.setenv("GOLDENCHECK_NATIVE", "0")
    py_result = st._compute_benford(values)
    monkeypatch.setenv("GOLDENCHECK_NATIVE", "1")
    native_result = st._compute_benford(values)

    assert py_result == native_result


def test_native_disabled_env_forces_python(monkeypatch: pytest.MonkeyPatch) -> None:
    """GOLDENCHECK_NATIVE=0 always uses the Python path, even when the ext is
    present -- so the result is unchanged whether or not native is installed."""
    from goldencheck.core._native_loader import native_enabled

    monkeypatch.setenv("GOLDENCHECK_NATIVE", "0")
    assert native_enabled("benford") is False
