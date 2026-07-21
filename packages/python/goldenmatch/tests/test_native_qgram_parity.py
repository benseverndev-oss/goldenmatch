"""Parity for the q-gram bucket kernel (score-core id 5).

qgram was the first Wave-1 scorer cut to a `-core` kernel (spec
`docs/superpowers/specs/2026-07-21-scorer-kernel-full-coverage-design.md`). Three
things must agree, and this file pins all three:

1. native `qgram_similarity` (Rust score-core) == pure-Python `_qgram_score_single`
   -- the kernel-vs-fallback contract the `GOLDENMATCH_NATIVE=auto` route relies on.
2. `_qgram_score_single` (the new bucket per-pair mirror) == `_qgram_score_matrix`
   diagonal/off-diagonal -- so making qgram fast-path eligible is output-neutral
   vs the old slow matrix path.
3. the bucket kernel `score_block_pairs` dispatching scorer id 5 == the per-pair
   mirror -- the end-to-end path the metric now counts.

Parity is asserted EXACTLY (not approx): both sides compute the same
`|A ∩ B| / |A ∪ B|` ratio of integer set counts (an f64 division of two ints),
so the results are bit-identical on the ASCII / common-Latin inputs a short-code
scorer sees. (Rust `to_lowercase` vs Python
`str.lower()` can differ on exotic codepoints -- the documented ASCII/Latin-scope
edge; the corpus stays in that scope.)
"""
from __future__ import annotations

import random

import pytest
from goldenmatch.core import _native_loader
from goldenmatch.core.scorer import _qgram_score_matrix, _qgram_score_single

_FIXED = [
    ("", ""), ("a", ""), ("", "b"), ("abc", "abc"), ("ABC", "abc"),
    ("abc", "abd"), ("abcd", "abce"), ("ab", "xy"),
    ("John Smith", "Jon Smyth"), ("Smith John", "John Smith"),
    ("SKU-1234", "SKU-1235"), ("widget", "widgets"), ("MacDonald", "Macdonald"),
    ("café", "cafe"), ("naïve", "naive"),
]


def _corpus() -> list[tuple[str, str]]:
    rng = random.Random(20260721)
    alphabet = "abcdefghijklmnopqrstuvwxyz ABCDEFGHIJ0123456789-#"
    def rand_str() -> str:
        return "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 14)))
    return _FIXED + [(rand_str(), rand_str()) for _ in range(1500)]


def test_qgram_native_matches_pure():
    n = _native_loader.native_module()
    if n is None or not hasattr(n, "qgram_similarity"):
        pytest.skip("native qgram kernel not built / wheel predates qgram_similarity")
    for a, b in _corpus():
        assert n.qgram_similarity(a, b) == _qgram_score_single(a, b), f"qgram {a!r} {b!r}"


def test_qgram_per_pair_mirror_matches_matrix():
    # Output-neutrality: the bucket per-pair mirror must equal the matrix path
    # it replaces for qgram configs, off-diagonal and on the diagonal.
    values = ["abc", "abd", "abc", "xyz", "abce", "", "ab"]
    mat = _qgram_score_matrix(values)
    for i, a in enumerate(values):
        assert mat[i, i] == 1.0
        for j in range(i + 1, len(values)):
            assert mat[i, j] == _qgram_score_single(a, values[j]), f"{a!r} {values[j]!r}"


def test_qgram_bucket_kernel_id5_matches_mirror():
    """score_block_pairs dispatching scorer id 5 == the per-pair mirror.

    One block, one qgram field, weight 1.0, threshold 0.0 so every pair emits;
    the kernel's per-pair score must equal `_qgram_score_single`.
    """
    n = _native_loader.native_module()
    if n is None or not hasattr(n, "qgram_similarity"):
        pytest.skip("native qgram kernel not built / wheel predates qgram_similarity")

    values = ["widget", "widgets", "gadget", "widget", "wdget"]
    row_ids = list(range(len(values)))
    sizes = [len(values)]              # one block holding every row
    field_values = [values]            # one field
    ids = [5]                          # qgram
    weights = [1.0]
    total_weight = 1.0
    threshold = 0.0
    emitted = n.score_block_pairs(
        row_ids, sizes, field_values, ids, weights, total_weight, threshold, []
    )
    got = {(min(a, b), max(a, b)): s for a, b, s in emitted}
    for i in range(len(values)):
        for j in range(i + 1, len(values)):
            expected = _qgram_score_single(values[i], values[j])
            if expected >= threshold:
                # Exact: one qgram field, weight 1.0, total_weight 1.0 -> the
                # kernel's emitted score is score_one(id 5) in f64 with no
                # downcast, so it is bit-identical to the pure mirror.
                assert got[(i, j)] == expected, f"{values[i]!r} {values[j]!r}"
