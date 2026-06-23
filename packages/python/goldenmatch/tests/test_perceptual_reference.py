"""Lock the perceptual-core golden fixture against the Python reference (ADR 0022).

``tests/fixtures/perceptual_golden.json`` is generated from ``perceptual.py`` by
``scripts/gen_perceptual_golden.py``. This suite asserts the reference still
reproduces it byte-for-byte (a tripwire against algorithm drift) and pins the
perceptual properties the crawl tier relies on: brightness/contrast/blur
invariance for content-rich images, discrimination of distinct media, and audio
amplitude invariance. The Rust crate will assert against the same fixture, so the
implementations are anchored to one source of truth.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from goldenmatch.core import perceptual

_FIXTURE = Path(__file__).parent / "fixtures" / "perceptual_golden.json"


def _load() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def _img(fixture: dict, name: str) -> list[list[int]]:
    for img in fixture["images"]:
        if img["name"] == name:
            return img["pixels"]
    raise KeyError(name)


# --------------------------------------------------------------------------- #
# golden parity                                                               #
# --------------------------------------------------------------------------- #
def test_fixture_params_match_constants():
    fx = _load()
    assert fx["image_params"] == {
        "resize": perceptual.IMG_RESIZE,
        "hash_size": perceptual.HASH_SIZE,
    }
    assert fx["audio_params"]["frame"] == perceptual.AUDIO_FRAME
    assert fx["audio_params"]["bands"] == perceptual.AUDIO_BANDS
    assert len(fx["images"]) >= 3 and len(fx["audio"]) >= 2


def test_reference_reproduces_image_fixture():
    for img in _load()["images"]:
        got = perceptual.phash_image(img["pixels"])
        assert hex(got) == img["phash"], f"phash drift for {img['name']}"
        # determinism: recompute is identical
        assert perceptual.phash_image(img["pixels"]) == got


def test_reference_reproduces_audio_fixture():
    fx = _load()
    scale = fx["pcm_scale"]
    for aud in fx["audio"]:
        samples = [s / scale for s in aud["pcm16"]]
        got = perceptual.fingerprint_audio(samples, aud["sample_rate"])
        assert [hex(w) for w in got] == aud["fingerprint"], f"fp drift for {aud['name']}"
        assert perceptual.fingerprint_audio(samples, aud["sample_rate"]) == got


# --------------------------------------------------------------------------- #
# perceptual properties (the reason the hash is useful as a match feature)     #
# --------------------------------------------------------------------------- #
def test_image_invariant_to_brightness_contrast_and_blur():
    base_grid = _img(_load(), "gratings_40x28")  # frequency-rich -> stable bits
    base = perceptual.phash_image(base_grid)

    affine = [[0.7 * v + 15 for v in row] for row in base_grid]  # contrast + brightness
    assert perceptual.hamming(base, perceptual.phash_image(affine)) <= 4

    h, w = len(base_grid), len(base_grid[0])
    blurred = [[0.0] * w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            acc = cnt = 0
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    yy, xx = y + dy, x + dx
                    if 0 <= yy < h and 0 <= xx < w:
                        acc += base_grid[yy][xx]
                        cnt += 1
            blurred[y][x] = acc / cnt
    assert perceptual.hamming(base, perceptual.phash_image(blurred)) <= 6


def test_image_distinct_inputs_are_far():
    fx = _load()
    a = perceptual.phash_image(_img(fx, "gradient_16x16"))
    b = perceptual.phash_image(_img(fx, "checker_24x24_c3"))
    assert perceptual.hamming(a, b) >= 16


def _sines(length: int, sample_rate: int, freqs: list[float]) -> list[float]:
    out = []
    for n in range(length):
        t = n / sample_rate
        out.append(sum(math.sin(2.0 * math.pi * f * t) for f in freqs) / len(freqs))
    return out


def test_audio_amplitude_invariance_and_discrimination():
    sr = 44100
    sig = _sines(12288, sr, [440.0, 660.0, 880.0])
    base = perceptual.fingerprint_audio(sig, sr)

    assert perceptual.audio_ber(base, base) == 0.0
    half = perceptual.fingerprint_audio([0.5 * v for v in sig], sr)
    assert perceptual.audio_ber(base, half) <= 0.05  # amplitude does not change the hash

    other = perceptual.fingerprint_audio(_sines(12288, sr, [1200.0, 1700.0]), sr)
    assert perceptual.audio_ber(base, other) > perceptual.audio_ber(base, half)


# --------------------------------------------------------------------------- #
# bit helpers + validation                                                    #
# --------------------------------------------------------------------------- #
def test_bit_helpers():
    assert perceptual.popcount(0) == 0
    assert perceptual.popcount(0b1011) == 3
    assert perceptual.hamming(0b1100, 0b1010) == 2


def test_audio_ber_empty_inputs():
    assert perceptual.audio_ber([], []) == 1.0


def test_image_validation():
    with pytest.raises(ValueError):
        perceptual.phash_image([])
    with pytest.raises(ValueError):
        perceptual.phash_image([[1, 2, 3], [4, 5]])  # ragged


def test_audio_validation():
    with pytest.raises(ValueError):
        perceptual.fingerprint_audio([0.0] * 100, 0)
