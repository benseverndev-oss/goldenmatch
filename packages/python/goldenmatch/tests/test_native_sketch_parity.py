"""Native <-> Python parity for the MinHash/LSH sketch kernel (#1081).

The native shims (`sketch_band_hashes_batch` / `sketch_signature_batch`)
delegate to `goldenmatch-sketch-core`, which is golden-vector-verified against
the Python reference. This test confirms the wired-up native module reproduces
the pure-Python reference byte-for-byte over a randomized sweep + edge cases.

Skips when the native kernel isn't built (local dev without `build_native.py`);
runs for real in CI's `native` lane where the wheel is freshly compiled.
"""
from __future__ import annotations

import random

import pytest
from goldenmatch.core import sketch
from goldenmatch.core._native_loader import native_available, native_module

pytestmark = pytest.mark.skipif(
    not native_available()
    or not hasattr(native_module() if native_available() else None, "sketch_band_hashes_batch"),
    reason="goldenmatch._native (with sketch symbols) not built",
)


_MODES = ["char", "word"]
_TEXT_POOL = [
    "",
    "   \t\n",  # whitespace-only
    "a",
    "hello world",
    "the quick brown fox jumps over the lazy dog",
    "héllo wörld",  # multibyte
    "東京タワー",  # CJK
    "foo foo foo bar bar",  # repeated tokens
    "a b c",  # NBSP is not a separator; ASCII space is
    "single",
]


def _random_texts(rng: random.Random, n: int) -> list[str]:
    return [rng.choice(_TEXT_POOL) for _ in range(n)]


def test_native_band_hashes_batch_matches_reference():
    nat = native_module()
    rng = random.Random(2026)
    for _ in range(40):
        texts = _random_texts(rng, rng.randint(1, 6))
        mode = rng.choice(_MODES)
        k = rng.randint(1, 4)
        num_bands = rng.choice([4, 8, 16])
        num_perms = num_bands * rng.choice([1, 2, 4])
        seed = rng.randint(0, 2**31)

        expected = sketch._band_hashes_batch_python(texts, mode, k, num_perms, num_bands, seed)
        got = nat.sketch_band_hashes_batch(texts, mode, k, num_perms, num_bands, seed)
        assert got == expected, (texts, mode, k, num_perms, num_bands, seed)


def test_native_signature_batch_matches_reference():
    nat = native_module()
    rng = random.Random(7)
    for _ in range(40):
        texts = _random_texts(rng, rng.randint(1, 6))
        mode = rng.choice(_MODES)
        k = rng.randint(1, 4)
        num_perms = rng.choice([8, 16, 32, 64])
        seed = rng.randint(0, 2**31)

        expected = sketch._signature_batch_python(texts, mode, k, num_perms, seed)
        got = nat.sketch_signature_batch(texts, mode, k, num_perms, seed)
        assert got == expected, (texts, mode, k, num_perms, seed)


def test_native_reproduces_golden_constants():
    nat = native_module()
    # The same end-to-end golden as the Rust/Python golden-constant tests.
    assert nat.sketch_band_hashes_batch(["hello world"], "char", 3, 8, 4, 42) == [
        [12901963457859849374, 4306753959614852008, 8435817867480225113, 7834504510243305493]
    ]


def test_public_dispatch_forced_native_matches_python(monkeypatch):
    # GOLDENMATCH_NATIVE=1 forces the native path through the public entry point.
    texts = ["hello world", "", "foo bar baz", "héllo"]
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "0")
    py = sketch.band_hashes_batch(texts, mode="word", k=2, num_perms=16, num_bands=8, seed=3)
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "1")
    nat = sketch.band_hashes_batch(texts, mode="word", k=2, num_perms=16, num_bands=8, seed=3)
    assert nat == py


def test_native_rejects_bad_params():
    nat = native_module()
    with pytest.raises(ValueError):
        nat.sketch_band_hashes_batch(["x"], "char", 0, 8, 4, 0)  # k < 1
    with pytest.raises(ValueError):
        nat.sketch_band_hashes_batch(["x"], "char", 2, 8, 3, 0)  # 8 not divisible by 3
    with pytest.raises(ValueError):
        nat.sketch_band_hashes_batch(["x"], "bigram", 2, 8, 4, 0)  # unknown mode
