"""Native <-> Python parity for the SimHash sketch kernel (#1082).

The native shim (``sketch_simhash_band_hashes_batch``) delegates to
``goldenmatch-sketch-core::simhash_band_hashes_batch``, which is golden-vector-
verified against the Python reference (``core/sketch.py``). This test confirms
the wired-up native module reproduces the pure-Python reference byte-for-byte
over a randomized sweep of dense f64 vectors + edge cases.

Skips when the native kernel isn't built (local dev without ``build_native.py``)
OR when the published wheel predates the ``sketch_simhash_band_hashes_batch``
symbol; runs for real in CI's ``native`` lane where the wheel is freshly compiled.
"""
from __future__ import annotations

import random

import pytest
from goldenmatch.core import sketch
from goldenmatch.core._native_loader import native_available, native_module

pytestmark = pytest.mark.skipif(
    not native_available()
    or not hasattr(
        native_module() if native_available() else None,
        "sketch_simhash_band_hashes_batch",
    ),
    reason="goldenmatch._native (with sketch_simhash_band_hashes_batch) not built",
)


def _random_vectors(rng: random.Random, n: int, dim: int) -> list[list[float]]:
    return [[rng.uniform(-1.0, 1.0) for _ in range(dim)] for _ in range(n)]


def test_native_simhash_band_hashes_batch_matches_reference():
    nat = native_module()
    rng = random.Random(2026)
    for _ in range(40):
        n = rng.randint(1, 6)
        dim = rng.choice([4, 8, 16, 32])
        num_bands = rng.choice([2, 4, 8])
        num_planes = num_bands * rng.choice([1, 2, 4])
        seed = rng.randint(0, 2**31)
        vectors = _random_vectors(rng, n, dim)

        expected = sketch._simhash_band_hashes_batch_python(
            vectors, num_planes, num_bands, seed
        )
        got = nat.sketch_simhash_band_hashes_batch(vectors, num_planes, num_bands, seed)
        assert got == expected, (n, dim, num_planes, num_bands, seed)


def test_native_simhash_handles_zero_and_mixed_vectors():
    nat = native_module()
    # All-zero vector -> all-ones signature sentinel; mixed-sign vector; the
    # batch entry must reproduce the per-row reference for both.
    vectors = [
        [0.0] * 8,
        [0.5, -0.3, 0.8, 0.1, -0.9, 0.4, -0.2, 0.7],
        [-1.0, 2.0, -3.0, 4.0, -5.0, 6.0, -7.0, 8.0],
    ]
    expected = sketch._simhash_band_hashes_batch_python(vectors, 8, 4, 42)
    got = nat.sketch_simhash_band_hashes_batch(vectors, 8, 4, 42)
    assert got == expected


def test_native_simhash_reproduces_golden_constants():
    nat = native_module()
    # Same end-to-end golden as the Rust/Python golden-constant tests:
    # V = [0.5, -0.3, 0.8, 0.1, -0.9, 0.4, -0.2, 0.7], num_planes=8, bands=4,
    # seed=42 -> signature [1,1,1,1,1,0,1,1], banded into 4 buckets.
    v = [0.5, -0.3, 0.8, 0.1, -0.9, 0.4, -0.2, 0.7]
    assert nat.sketch_simhash_band_hashes_batch([v], 8, 4, 42) == [
        [
            8326405673782927272,
            10087387020540333614,
            407431194778926956,
            13491348438230804516,
        ]
    ]


def test_native_simhash_empty_batch():
    nat = native_module()
    assert nat.sketch_simhash_band_hashes_batch([], 8, 4, 0) == []


def test_public_dispatch_forced_native_matches_python(monkeypatch):
    # GOLDENMATCH_NATIVE=1 forces the native path through the public entry point.
    vectors = [
        [0.5, -0.3, 0.8, 0.1, -0.9, 0.4, -0.2, 0.7],
        [0.0] * 8,
        [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8],
    ]
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "0")
    py = sketch.simhash_band_hashes_batch(vectors, num_planes=16, num_bands=8, seed=3)
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "1")
    nat = sketch.simhash_band_hashes_batch(vectors, num_planes=16, num_bands=8, seed=3)
    assert nat == py


def test_native_simhash_rejects_bad_params():
    nat = native_module()
    with pytest.raises(ValueError):
        nat.sketch_simhash_band_hashes_batch([[0.0] * 8], 8, 0, 0)  # num_bands == 0
    with pytest.raises(ValueError):
        nat.sketch_simhash_band_hashes_batch([[0.0] * 8], 8, 3, 0)  # 8 not divisible by 3
