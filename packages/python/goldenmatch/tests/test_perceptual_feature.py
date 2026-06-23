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
