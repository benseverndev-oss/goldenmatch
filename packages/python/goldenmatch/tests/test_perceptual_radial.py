"""Rotation/crop-aware radial-variance image feature (ADR 0022, walk-tier slice 1).

pHash is photometric, not geometric: the bench harness measured 0.0 recall on
rotation and crop (finding 1). The radial-variance profile + angular-aligned
comparison (`core.perceptual.radial_variance` / `radial_align_similarity`) is the
proven rotation-AWARE answer — it keeps orientation in the feature vector (so it
discriminates) and searches the cyclic angular shift (so it absorbs rotation),
exactly as `audio_ber_aligned` searches the time offset. These tests lock the
blind-spot closure: rotate 0.0->~0.85+, crop 0.0->~0.9+, photometric stays high,
unrelated stays low.
"""
from __future__ import annotations

import math

from goldenmatch.core import perceptual


# --------------------------------------------------------------------------- #
# self-contained image generators (mirror the bench-suite transforms)         #
# --------------------------------------------------------------------------- #
def _pattern(seed: int, h: int = 48, w: int = 48) -> list[list[int]]:
    fx = 1.5 + (seed % 5) * 0.4
    fy = 2.0 + (seed % 7) * 0.3
    fd = 2.2 + (seed % 3) * 0.6
    ph = seed * 0.7
    return [
        [
            max(0, min(255, int(round(
                128 + 45 * math.sin(x / fx + ph) + 40 * math.sin(y / fy)
                + 30 * math.sin((x + y) / fd)
            ))))
            for x in range(w)
        ]
        for y in range(h)
    ]


def _rotate(g: list[list[int]], deg: float = 8.0) -> list[list[int]]:
    h, w = len(g), len(g[0])
    cy, cx = (h - 1) / 2, (w - 1) / 2
    rad = math.radians(deg)
    cos, sin = math.cos(rad), math.sin(rad)
    out = [[0] * w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            sx = cx + (x - cx) * cos - (y - cy) * sin
            sy = cy + (x - cx) * sin + (y - cy) * cos
            ix, iy = int(round(sx)), int(round(sy))
            out[y][x] = g[iy][ix] if 0 <= iy < h and 0 <= ix < w else 128
    return out


def _crop(g: list[list[int]]) -> list[list[int]]:
    h, w = len(g), len(g[0])
    my, mx = h // 16, w // 16  # ~88% center crop
    return [row[mx : w - mx] for row in g[my : h - my]]


def _brightness(g: list[list[int]]) -> list[list[int]]:
    return [[max(0, min(255, int(round(v * 0.9 + 22)))) for v in row] for row in g]


def _rv(img) -> list[float]:
    return perceptual.radial_variance(img)


# --------------------------------------------------------------------------- #
# profile shape + determinism                                                 #
# --------------------------------------------------------------------------- #
def test_radial_profile_shape_and_determinism():
    img = _pattern(0)
    p = _rv(img)
    assert len(p) == perceptual.RADIAL_ANGLES
    assert all(v >= 0.0 for v in p)  # variances are non-negative
    assert p == _rv(img)  # deterministic


# --------------------------------------------------------------------------- #
# the headline: rotation + crop blind spot is closed                          #
# --------------------------------------------------------------------------- #
def test_radial_recovers_rotation_and_crop():
    rot_sims, crop_sims, unrel_sims = [], [], []
    for seed in range(8):
        base = _pattern(seed)
        pb = _rv(base)
        rot_sims.append(perceptual.radial_align_similarity(pb, _rv(_rotate(base))))
        crop_sims.append(perceptual.radial_align_similarity(pb, _rv(_crop(base))))
        unrel_sims.append(
            perceptual.radial_align_similarity(pb, _rv(_pattern(seed + 50)))
        )
    # rotation and crop — pHash scores these at 0.0 — are now strongly recalled
    assert min(rot_sims) > 0.80
    assert min(crop_sims) > 0.85
    # and cleanly separated from unrelated images (the discrimination margin)
    assert max(unrel_sims) < 0.70
    assert min(rot_sims) > max(unrel_sims)
    assert min(crop_sims) > max(unrel_sims)


def test_radial_photometric_invariance_preserved():
    # the feature must not regress the cases pHash already handled
    for seed in range(5):
        base = _pattern(seed)
        pb = _rv(base)
        assert perceptual.radial_align_similarity(pb, _rv(_brightness(base))) > 0.95


def test_radial_alignment_beats_raw_for_rotation():
    # the cyclic-shift search is load-bearing: a rotated profile aligns far better
    # than the un-shifted (shift-0) comparison.
    base = _pattern(2)
    pb = _rv(base)
    pr = _rv(_rotate(base, 8.0))
    aligned = perceptual.radial_align_similarity(pb, pr)
    raw = perceptual._pearson(pb, pr)  # shift-0 only
    assert aligned >= raw
    assert aligned > 0.80


# --------------------------------------------------------------------------- #
# canonical hex column form                                                   #
# --------------------------------------------------------------------------- #
def test_radial_hex_roundtrip_preserves_similarity():
    base = _pattern(3)
    p = _rv(base)
    h = perceptual.radial_hex(p)
    assert len(h) == 2 * perceptual.RADIAL_ANGLES  # 2 hex chars / int8 bin
    back = perceptual.radial_from_hex(h)
    assert len(back) == perceptual.RADIAL_ANGLES
    # affine-invariant comparison => quantised profile scores ~identically
    sim = perceptual.radial_align_similarity(p, [float(v) for v in back])
    assert sim > 0.99
    # 0x prefix tolerated
    assert perceptual.radial_from_hex("0x" + h) == back


def test_radial_hex_constant_profile_is_zero():
    assert perceptual.radial_hex([5.0] * perceptual.RADIAL_ANGLES) == "00" * perceptual.RADIAL_ANGLES


def test_radial_align_edge_cases():
    assert perceptual.radial_align_similarity([], []) == 0.0
    assert perceptual.radial_align_similarity([1.0, 2.0, 3.0], [1.0, 2.0]) == 0.0  # length mismatch
    # identical profile -> 1.0
    p = _rv(_pattern(1))
    assert perceptual.radial_align_similarity(p, p) == 1.0


# --------------------------------------------------------------------------- #
# the `radial` pipeline scorer (slice 3) over the hex column form              #
# --------------------------------------------------------------------------- #
def _rhex(img) -> str:
    return perceptual.radial_hex(_rv(img))


def test_radial_scorer_via_score_field():
    from goldenmatch.core.scorer import score_field

    base = _pattern(4)
    ha = _rhex(base)
    hb = _rhex(_rotate(base))  # geometric variant
    hd = _rhex(_pattern(54))  # unrelated
    assert score_field(ha, ha, "radial") == 1.0
    # the rotated variant scores far above the unrelated image (the evidence)
    assert score_field(ha, hb, "radial") > 0.80
    assert score_field(ha, hb, "radial") > score_field(ha, hd, "radial")
    # 0x prefix tolerated, None short-circuits to None (generic guard)
    assert score_field("0x" + ha, ha, "radial") == 1.0
    assert score_field(None, ha, "radial") is None


def test_radial_score_matrix():
    from goldenmatch.core.scorer import _radial_score_matrix

    base = _pattern(6)
    vals = [_rhex(base), _rhex(_rotate(base)), _rhex(_pattern(56)), None]
    m = _radial_score_matrix(vals)
    assert m.shape == (4, 4)
    assert m[0, 0] == 1.0 and m[1, 1] == 1.0
    assert m[0, 1] == m[1, 0] and m[0, 1] > 0.80  # base <-> rotated, symmetric
    assert m[0, 1] > m[0, 2]  # variant beats unrelated
    assert (m[3] == 0.0).all() and (m[:, 3] == 0.0).all()  # None scores 0 everywhere
