"""Perceptual hashing as a pipeline match feature (ADR 0022, slice 3).

The ``phash`` scorer (hamming similarity over a hex perceptual hash) + the
``perceptual`` banded-hamming-LSH blocking strategy turn an image-pHash column
into a first-class match feature ("modality as evidence"). Verified end-to-end on
real pHashes of image variants produced by ``core.perceptual.phash_image``.
"""
from __future__ import annotations

import math

import polars as pl
import pytest
from goldenmatch.config.schemas import BlockingConfig, PerceptualKeyConfig
from goldenmatch.core import perceptual
from goldenmatch.core.blocker import build_blocks
from goldenmatch.core.perceptual_blocker import PerceptualLSHBlocker
from goldenmatch.core.scorer import _phash_score_matrix, score_field


# --------------------------------------------------------------------------- #
# phash scorer                                                                #
# --------------------------------------------------------------------------- #
def test_phash_scorer_identical_and_distance():
    h = "eed1ac9ad43e9441"
    assert score_field(h, h, "phash") == 1.0
    flipped = format(int(h, 16) ^ 1, "016x")  # one bit -> 1/64 distance
    assert score_field(h, flipped, "phash") == pytest.approx(1.0 - 1 / 64)
    assert score_field("0x" + h, h, "phash") == 1.0  # 0x prefix tolerated


def test_phash_scorer_none_returns_none():
    assert score_field(None, "ffffffffffffffff", "phash") is None


def test_phash_score_matrix():
    a = "00000000000000ff"
    b = "00000000000000fe"  # 1 bit off
    c = "ffffffffffffff00"  # far
    m = _phash_score_matrix([a, b, c])
    assert m[0, 0] == pytest.approx(1.0)
    assert m[0, 1] == pytest.approx(1.0 - 1 / 64)
    assert m[0, 2] < 0.5
    m2 = _phash_score_matrix([a, None])  # None scores 0 against everything
    assert m2[0, 1] == 0.0


# --------------------------------------------------------------------------- #
# perceptual blocker (banded hamming-LSH)                                     #
# --------------------------------------------------------------------------- #
def test_perceptual_blocker_groups_near_separates_far():
    blocker = PerceptualLSHBlocker(num_bands=8, hash_bits=64)
    base = 0x0123456789ABCDEF
    near = base ^ 0b1  # one bit in the lowest band -> 7 bands untouched
    far = ~base & ((1 << 64) - 1)  # every band differs
    pairs = blocker.candidate_pairs([base, near, far])
    assert (0, 1) in pairs
    assert (0, 2) not in pairs and (1, 2) not in pairs


def test_perceptual_blocker_skips_nulls():
    blocker = PerceptualLSHBlocker(num_bands=8, hash_bits=64)
    assert blocker.candidate_pairs([0x1234, None, None]) == set()


def test_perceptual_key_config_validation():
    PerceptualKeyConfig(column="ph", num_bands=8, hash_bits=64)  # ok
    with pytest.raises(ValueError):
        PerceptualKeyConfig(column="ph", num_bands=7, hash_bits=64)  # 64 % 7 != 0


def test_perceptual_key_config_default_is_recall_driven():
    # The default is the recall-target band count (16), not the old reduction-biased
    # 8 — a near-dup MEDIA blocker prioritises recall (a missed dup is unrecoverable;
    # the scorer filters the extra candidates cheaply). Bench: 0.72 -> 0.97 recall.
    assert PerceptualKeyConfig(column="ph").num_bands == 16


# --------------------------------------------------------------------------- #
# recall-target band recommender (finding 2: blocking recall vs reduction)     #
# --------------------------------------------------------------------------- #
def test_recommend_num_bands_meets_recall_target_at_image_radius():
    from goldenmatch.core.perceptual_blocker import (
        lsh_collision_probability,
        recommend_num_bands,
    )

    # 0.85 image threshold => 0.15 hamming radius; at 0.95 target the cheapest
    # divisor band count that recalls the radius is 16 (matches the bench sweep).
    nb = recommend_num_bands(64, 1.0 - 0.85, 0.95)
    assert nb == 16
    assert lsh_collision_probability(0.15, nb, 64) >= 0.95
    # and it is the CHEAPEST such count (8 bands does not reach the target).
    assert lsh_collision_probability(0.15, 8, 64) < 0.95


def test_recommend_num_bands_monotone_in_target_and_radius():
    from goldenmatch.core.perceptual_blocker import recommend_num_bands

    # a stricter recall target never needs fewer bands
    assert recommend_num_bands(64, 0.15, 0.99) >= recommend_num_bands(64, 0.15, 0.90)
    # a wider near-dup radius (lower scorer threshold) never needs fewer bands
    assert recommend_num_bands(64, 0.25, 0.95) >= recommend_num_bands(64, 0.10, 0.95)
    # result always evenly divides hash_bits (PerceptualKeyConfig invariant)
    assert 64 % recommend_num_bands(64, 0.15, 0.95) == 0


def test_recommend_num_bands_lifts_blocking_recall():
    from goldenmatch.core.perceptual_blocker import recommend_num_bands

    # The recommended count recalls more true near-dup pairs than the old default 8.
    base = 0x0123456789ABCDEF
    # 12 near-duplicates at a ~2/64 hamming radius (well inside the 0.85 threshold)
    hashes = [base] + [base ^ ((1 << k) | (1 << ((k + 7) % 64))) for k in range(12)]
    gt = {(0, i) for i in range(1, len(hashes))}
    nb = recommend_num_bands(64, 0.15, 0.95)
    recalled_hi = len(PerceptualLSHBlocker(nb, 64).candidate_pairs(hashes) & gt)
    recalled_8 = len(PerceptualLSHBlocker(8, 64).candidate_pairs(hashes) & gt)
    assert recalled_hi >= recalled_8


# --------------------------------------------------------------------------- #
# end-to-end on real image pHashes                                            #
# --------------------------------------------------------------------------- #
def _gratings(h: int, w: int) -> list[list[int]]:
    return [
        [
            max(
                0,
                min(
                    255,
                    int(
                        128
                        + 40 * math.sin(x / 2.0)
                        + 35 * math.sin(y / 3.0)
                        + 30 * math.sin((x + y) / 2.5)
                    ),
                ),
            )
            for x in range(w)
        ]
        for y in range(h)
    ]


def _blur(grid: list[list[int]]) -> list[list[float]]:
    h, w = len(grid), len(grid[0])
    out = [[0.0] * w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            acc = cnt = 0
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    yy, xx = y + dy, x + dx
                    if 0 <= yy < h and 0 <= xx < w:
                        acc += grid[yy][xx]
                        cnt += 1
            out[y][x] = acc / cnt
    return out


def test_perceptual_feature_blocks_and_scores_image_variants():
    base = _gratings(48, 48)
    blurred = _blur(base)  # a perceptual variant of the same image
    distinct = [[(x * 7 + y * 13) % 256 for x in range(40)] for y in range(40)]

    h0 = perceptual.phash_hex(perceptual.phash_image(base))
    hb = perceptual.phash_hex(perceptual.phash_image(blurred))
    hd = perceptual.phash_hex(perceptual.phash_image(distinct))

    df = pl.DataFrame({"__row_id__": [0, 1, 2], "ph": [h0, hb, hd]})
    cfg = BlockingConfig(strategy="perceptual", perceptual=PerceptualKeyConfig(column="ph"))
    blocks = build_blocks(df.lazy(), cfg)

    member_sets = [set(b.df.collect()["__row_id__"].to_list()) for b in blocks]
    # the variant pair co-occurs in at least one block (candidate generation)
    assert any({0, 1} <= s for s in member_sets)
    # and the scorer ranks the variant above the distinct image (the evidence)
    assert score_field(h0, hb, "phash") > score_field(h0, hd, "phash")


# --------------------------------------------------------------------------- #
# recall gate — every image variant must block with its source                #
# --------------------------------------------------------------------------- #
def _pattern(fx: float, fy: float, fd: float) -> list[list[int]]:
    return [
        [
            max(0, min(255, int(128 + 45 * math.sin(x / fx) + 40 * math.sin(y / fy)
                                + 30 * math.sin((x + y) / fd))))
            for x in range(44)
        ]
        for y in range(44)
    ]


def test_perceptual_blocking_recall_gate():
    bases = [
        _pattern(2.0, 3.0, 2.5),
        _pattern(1.5, 4.0, 3.5),
        _pattern(3.0, 2.0, 2.0),
        _pattern(2.5, 2.5, 4.5),
        _pattern(1.8, 3.3, 2.8),
    ]
    ids: list[int] = []
    hashes: list[str] = []
    for i, b in enumerate(bases):
        ids += [2 * i, 2 * i + 1]
        hashes += [
            perceptual.phash_hex(perceptual.phash_image(b)),
            perceptual.phash_hex(perceptual.phash_image(_blur(b))),
        ]
    df = pl.DataFrame({"__row_id__": ids, "ph": hashes})
    cfg = BlockingConfig(strategy="perceptual", perceptual=PerceptualKeyConfig(column="ph"))
    member_sets = [set(b.df.collect()["__row_id__"].to_list()) for b in build_blocks(df.lazy(), cfg)]
    for i in range(len(bases)):  # recall: each variant pair co-occurs in a block
        assert any({2 * i, 2 * i + 1} <= s for s in member_sets), f"variant {i} not blocked"


# --------------------------------------------------------------------------- #
# audio fingerprint scorer (offset-aligned BER)                               #
# --------------------------------------------------------------------------- #
def _sines(length: int, sample_rate: int, freqs: list[float]) -> list[float]:
    return [
        sum(math.sin(2.0 * math.pi * f * n / sample_rate) for f in freqs) / len(freqs)
        for n in range(length)
    ]


def test_audio_fp_hex_roundtrip():
    fp = [0x0A004AC4, 0xFFFB53BF, 0x00000001]
    assert perceptual.audio_fp_from_hex(perceptual.audio_fp_hex(fp)) == fp


def test_audio_fp_scorer_aligned_and_discriminating():
    sr = 44100
    sig = _sines(20000, sr, [440.0, 660.0, 880.0])
    ha = perceptual.audio_fp_hex(perceptual.fingerprint_audio(sig, sr))
    assert score_field(ha, ha, "audio_fp") == 1.0
    # a recording that starts ~2 frames later still aligns to a high score
    shifted = perceptual.audio_fp_hex(perceptual.fingerprint_audio(sig[4096:], sr))
    aligned = score_field(ha, shifted, "audio_fp")
    other = perceptual.audio_fp_hex(perceptual.fingerprint_audio(_sines(20000, sr, [1200.0, 1700.0]), sr))
    assert aligned > 0.8
    assert score_field(ha, other, "audio_fp") < aligned
