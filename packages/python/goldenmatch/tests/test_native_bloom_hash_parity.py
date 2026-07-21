"""Parity for the dice / jaccard / phash bucket kernels (score-core ids 9/10/11)
vs the pure Python references `_dice_score_single` / `_jaccard_score_single` /
`_phash_score_single`.

These are the bloom-hex + hex-hamming scorers. Wave 3 kernelizes them as per-pair
`score_one` ids (integer popcount + one f64 divide -- byte-exact with the single
functions, unlike the numpy MATRIX forms which compute in float32). See
docs/superpowers/specs/2026-07-21-block-aware-bucket-kernel-design.md.

- dice/jaccard: padding-invariant (popcount denominators), so the kernel is
  byte-parity regardless of hash length.
- phash: matches the PAIRWISE `_phash_score_single` (nbits = the longer of the two
  hashes), NOT the block-global `_phash_score_matrix` -- the Option-A choice. This
  makes phash bucket-eligible for the first time (it previously declined to the
  matrix path).

Parity is asserted over thousands of random hex pairs (fixed-width + variable
length) plus edges: empty, odd-length (phash left-pads), `0x` prefix, and
unparseable hex (the kernel returns 0.0 where the single functions raise -- an
edge that never occurs for real CLK / pHash inputs, pinned here explicitly).
"""
from __future__ import annotations

import random

import pytest
from goldenmatch.core import _native_loader
from goldenmatch.core import scorer as _scorer


def _corpus() -> list[tuple[str, str]]:
    rng = random.Random(20260721)
    hexchars = "0123456789abcdef"
    def rand_hex(nbytes: int) -> str:
        return "".join(rng.choice(hexchars) for _ in range(nbytes * 2))
    pairs: list[tuple[str, str]] = []
    # Fixed-width (the normal CLK / 64-bit pHash case) dominates; some variable.
    for _ in range(3000):
        nb = rng.choice([1, 2, 8, 8, 8, 16, 16])
        a = rand_hex(nb)
        b = rand_hex(rng.choice([nb, nb, rng.randint(1, 16)]))
        pairs.append((a, b))
    # Mixed-case + edges -- all VALID even-length hex (no 0x prefix, no odd
    # length), since dice/jaccard don't normalize and `bytes.fromhex` raises
    # otherwise. phash's own normalize-edges are tested separately below.
    pairs += [
        ("ABCD", "abcd"), ("FFFF", "0F0F"), ("00", "00"), ("00", "ff"),
        ("abcd", "abcd"), ("ab", "abcd"), ("", ""), ("abcd", ""),
    ]
    return pairs


def test_dice_native_matches_pure_mirror():
    n = _native_loader.native_module()
    if n is None or not hasattr(n, "dice_similarity"):
        pytest.skip("native dice kernel not built / wheel predates dice_similarity")
    for a, b in _corpus():
        assert n.dice_similarity(a, b) == _scorer._dice_score_single(a, b), f"dice {a!r} {b!r}"


def test_jaccard_native_matches_pure_mirror():
    n = _native_loader.native_module()
    if n is None or not hasattr(n, "jaccard_similarity"):
        pytest.skip("native jaccard kernel not built")
    for a, b in _corpus():
        assert n.jaccard_similarity(a, b) == _scorer._jaccard_score_single(a, b), f"jaccard {a!r} {b!r}"


def test_phash_native_matches_pure_pairwise_mirror():
    n = _native_loader.native_module()
    if n is None or not hasattr(n, "phash_similarity"):
        pytest.skip("native phash kernel not built")
    for a, b in _corpus():
        assert n.phash_similarity(a, b) == _scorer._phash_score_single(a, b), f"phash {a!r} {b!r}"
    # phash's own normalize edges: odd length is left-padded ("f" -> "0f"), and a
    # leading `0x`/`0X` is stripped -- inputs dice/jaccard would raise on.
    for a, b in [("f", "f"), ("0x00ff", "ff00"), ("0X0f", "0f"), ("f", "ff")]:
        assert n.phash_similarity(a, b) == _scorer._phash_score_single(a, b), f"phash edge {a!r} {b!r}"


def test_bloom_hash_invalid_hex_is_zero_not_raise():
    # The single functions RAISE on unparseable hex; the score_one kernel can't
    # raise, so it returns 0.0 (never crashes the block loop). This edge doesn't
    # occur for real CLK / pHash inputs -- pinned so the divergence is explicit.
    n = _native_loader.native_module()
    if n is None or not hasattr(n, "dice_similarity"):
        pytest.skip("native bloom/hash kernels not built")
    for bad in ("zz", "xyz", "gg"):
        assert n.dice_similarity(bad, "abcd") == 0.0
        assert n.jaccard_similarity(bad, "abcd") == 0.0
        assert n.phash_similarity(bad, "abcd") == 0.0
        with pytest.raises(ValueError):
            _scorer._dice_score_single(bad, "abcd")


def test_bloom_hash_bucket_kernel_ids_9_10_11_match_mirror():
    """score_block_pairs dispatching ids 9/10/11 == the pure per-pair mirrors.

    One block per scorer, weight 1.0, threshold 0.0 so every pair emits.
    """
    n = _native_loader.native_module()
    if n is None or not hasattr(n, "dice_similarity"):
        pytest.skip("native bloom/hash kernels not built")
    values = ["abcd", "abce", "0f0f", "ffff", "1234"]
    row_ids = list(range(len(values)))
    for scorer_id, mirror in (
        (9, _scorer._dice_score_single),
        (10, _scorer._jaccard_score_single),
        (11, _scorer._phash_score_single),
    ):
        emitted = n.score_block_pairs(
            row_ids, [len(values)], [values], [scorer_id], [1.0], 1.0, 0.0, []
        )
        got = {(min(a, b), max(a, b)): s for a, b, s in emitted}
        for i in range(len(values)):
            for j in range(i + 1, len(values)):
                assert got[(i, j)] == mirror(values[i], values[j]), (
                    f"id={scorer_id} {values[i]!r} {values[j]!r}"
                )
