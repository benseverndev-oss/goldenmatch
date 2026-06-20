"""Golden-constant tests for the SimHash pure-Python reference (sketch.py).

These constants are the cross-language parity oracle for #1082: the Rust crate,
the native binding, and the TS port must all reproduce them. They were computed
from the reference algorithm and verified independently.
"""
from __future__ import annotations

import pytest
from goldenmatch.core import sketch

# A fixed mixed-sign dense vector exercised across the golden constants.
_V = [0.5, -0.3, 0.8, 0.1, -0.9, 0.4, -0.2, 0.7]


# ---- signature golden constants ----


def test_simhash_signature_planes8_seed42():
    assert sketch.simhash_signature(_V, num_planes=8, seed=42) == [1, 1, 1, 1, 1, 0, 1, 1]


def test_simhash_signature_planes16_seed7():
    assert sketch.simhash_signature(_V, num_planes=16, seed=7) == [
        1, 1, 0, 0, 1, 1, 1, 0, 0, 1, 0, 1, 1, 0, 1, 1,
    ]


def test_simhash_signature_zero_vector_all_ones():
    # Every dot is exactly 0.0; the tie (dot >= 0.0) resolves to 1.
    assert sketch.simhash_signature([0.0] * 8, 8, 42) == [1, 1, 1, 1, 1, 1, 1, 1]


def test_simhash_signature_is_bits_only():
    sig = sketch.simhash_signature(_V, 8, 42)
    assert all(b in (0, 1) for b in sig)
    assert len(sig) == 8


# ---- band hashes golden constants ----


def test_simhash_band_hashes_golden():
    assert sketch.simhash_band_hashes([1, 1, 1, 1, 1, 0, 1, 1], num_bands=4) == [
        8326405673782927272,
        10087387020540333614,
        407431194778926956,
        13491348438230804516,
    ]


def test_simhash_band_hashes_requires_divisible():
    with pytest.raises(ValueError):
        sketch.simhash_band_hashes([1, 0, 1, 1, 0, 1, 0, 1], 3)


def test_simhash_band_hashes_zero_bands_raises():
    with pytest.raises(ValueError):
        sketch.simhash_band_hashes([1, 0, 1, 0], 0)


# ---- empty / edge handling ----


def test_simhash_signature_empty_vector():
    # No dimensions -> every plane's dot is the empty sum 0.0 -> tie -> 1.
    assert sketch.simhash_signature([], 4, 1) == [1, 1, 1, 1]


def test_simhash_band_hashes_batch_matches_singles():
    vectors = [
        [0.5, -0.3, 0.8, 0.1, -0.9, 0.4, -0.2, 0.7],
        [0.0] * 8,
        [-1.0, 2.0, -3.0, 4.0, -5.0, 6.0, -7.0, 8.0],
    ]
    batch = sketch.simhash_band_hashes_batch(vectors, num_planes=8, num_bands=4, seed=42)
    singles = [
        sketch.simhash_band_hashes(sketch.simhash_signature(v, 8, 42), 4) for v in vectors
    ]
    assert batch == singles


def test_simhash_band_hashes_batch_empty():
    assert sketch.simhash_band_hashes_batch([], num_planes=8, num_bands=4, seed=42) == []
