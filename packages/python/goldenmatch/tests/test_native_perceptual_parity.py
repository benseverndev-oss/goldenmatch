"""Native <-> Python parity for the perceptual media-hash kernel (ADR 0022).

The native shims (`perceptual_phash_image` / `perceptual_phash_batch` /
`perceptual_fingerprint_audio`) delegate to `goldenmatch-perceptual-core`, which
is golden-vector-verified against the Python reference. This test confirms the
wired-up native module reproduces the pure-Python reference byte-for-byte over a
randomized sweep + the committed fixture + edge cases.

Skips when the native kernel isn't built (local dev without `build_native.py`);
runs for real in CI's `native` lane where the wheel is freshly compiled.
"""
from __future__ import annotations

import json
import math
import random
import struct
from pathlib import Path

import pytest
from goldenmatch.core import perceptual
from goldenmatch.core._native_loader import native_available, native_module

pytestmark = pytest.mark.skipif(
    not native_available()
    or not hasattr(
        native_module() if native_available() else None, "perceptual_phash_image"
    ),
    reason="goldenmatch._native (with perceptual symbols) not built",
)

_FIXTURE = Path(__file__).parent / "fixtures" / "perceptual_golden.json"


def _rand_grid(rng: random.Random) -> list[list[float]]:
    h, w = rng.randint(2, 40), rng.randint(2, 40)
    return [[float(rng.randint(0, 255)) for _ in range(w)] for _ in range(h)]


def _sines(length: int, sample_rate: int, freqs: list[float]) -> list[float]:
    return [
        sum(math.sin(2.0 * math.pi * f * n / sample_rate) for f in freqs) / len(freqs)
        for n in range(length)
    ]


def test_native_phash_matches_python_reference():
    nat = native_module()
    rng = random.Random(2026)
    for _ in range(60):
        grid = _rand_grid(rng)
        assert nat.perceptual_phash_image(grid) == perceptual._phash_image_python(grid)


def test_native_phash_batch_matches_per_image():
    nat = native_module()
    rng = random.Random(11)
    grids = [_rand_grid(rng) for _ in range(8)]
    assert nat.perceptual_phash_batch(grids) == [
        perceptual._phash_image_python(g) for g in grids
    ]


def test_native_fingerprint_audio_matches_python_reference():
    nat = native_module()
    rng = random.Random(7)
    for _ in range(12):
        sr = rng.choice([8000, 16000, 22050, 44100, 48000])
        length = rng.choice([4000, 6144, 8192, 12288])
        freqs = [rng.uniform(200.0, 3000.0) for _ in range(rng.randint(1, 3))]
        sig = _sines(length, sr, freqs)
        assert nat.perceptual_fingerprint_audio(sig, sr) == perceptual._fingerprint_audio_python(
            sig, sr
        )


def test_native_radial_matches_python_reference():
    nat = native_module()
    rng = random.Random(515)
    for _ in range(60):
        grid = _rand_grid(rng)
        assert nat.perceptual_radial_variance(grid) == perceptual._radial_variance_python(grid)


def test_native_reproduces_golden_fixture():
    nat = native_module()
    fx = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    for img in fx["images"]:
        grid = [[float(v) for v in row] for row in img["pixels"]]
        assert hex(nat.perceptual_phash_image(grid)) == img["phash"], img["name"]
        # radial profile is stored as hex f64 bit patterns -> bit-exact compare
        want = [struct.unpack("<d", struct.pack("<Q", int(h, 16)))[0] for h in img["radial"]]
        assert nat.perceptual_radial_variance(grid) == want, img["name"]
    scale = fx["pcm_scale"]
    for aud in fx["audio"]:
        samples = [s / scale for s in aud["pcm16"]]
        got = [hex(w) for w in nat.perceptual_fingerprint_audio(samples, aud["sample_rate"])]
        assert got == aud["fingerprint"], aud["name"]


def test_native_validation_parity():
    nat = native_module()
    with pytest.raises(ValueError):
        nat.perceptual_phash_image([])
    with pytest.raises(ValueError):
        nat.perceptual_phash_image([[1.0, 2.0], [3.0]])  # ragged
    with pytest.raises(ValueError):
        nat.perceptual_fingerprint_audio([0.0] * 100, 0)


def test_dispatch_uses_native_under_force(monkeypatch):
    """With GOLDENMATCH_NATIVE=1 the public entry points return the native result."""
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "1")
    nat = native_module()
    grid = _rand_grid(random.Random(99))
    assert perceptual.phash_image(grid) == nat.perceptual_phash_image(grid)
    assert perceptual.radial_variance(grid) == nat.perceptual_radial_variance(grid)
    sig = _sines(8192, 44100, [440.0, 660.0])
    assert perceptual.fingerprint_audio(sig, 44100) == nat.perceptual_fingerprint_audio(
        sig, 44100
    )
