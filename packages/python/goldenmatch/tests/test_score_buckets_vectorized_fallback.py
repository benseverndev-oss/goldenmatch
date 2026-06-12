"""Byte-parity gate for the bucket vectorized fast-path lane (_score_block_vec).

The lane replaces _score_one_bucket_fast's Python per-pair double loop with one
batched NxN matrix per field for mid-sized blocks. It is only allowed to ship a
scorer whose batched matrix is byte-identical to the per-pair score_pair -- the
ensemble decline in _resolve_score_pair_callable is the cautionary tale of a
per-pair reimpl that silently diverged and dropped recall. This test asserts,
scorer by scorer, that the lane emits the SAME pairs in the SAME order with the
SAME float bits as a reference per-pair loop built from the production resolver.
"""
from __future__ import annotations

import math

import pytest

from goldenmatch.backends.score_buckets import (
    _VEC_SUPPORTED,
    _resolve_score_pair_callable,
    _score_block_vec,
)

# A block of realistic-ish strings with near-duplicates so scores span the
# whole 0..1 range and threshold decisions are exercised at the edges.
_NAMES = [
    "Smith", "Smyth", "Smithe", "Smit", "Jones", "Jonas", "Jonsen",
    "Brown", "Browne", "Braun", "Robinson", "Robbinson", "Robins",
    "Anderson", "Andersen", "Andersson", "Smith", "Jones",
]


def _ref_per_pair(row_ids, field_arrays, scorer_names, weights, offset, end,
                  total_weight, threshold, frozen_exclude):
    """Reference mirror of _score_one_bucket_fast's per-pair branch (n_ne == 0,
    no nulls): same resolver, same arithmetic order, same emit shape."""
    fns = [_resolve_score_pair_callable(n) for n in scorer_names]
    out = []
    for i in range(offset, end - 1):
        ri = row_ids[i]
        for j in range(i + 1, end):
            rj = row_ids[j]
            pair_key = (ri, rj) if ri < rj else (rj, ri)
            if pair_key in frozen_exclude:
                continue
            score_sum = 0.0
            weight_sum = 0.0
            for f, (fn, w) in enumerate(zip(fns, weights)):
                va = field_arrays[f][i]
                vb = field_arrays[f][j]
                if va is None or vb is None:
                    continue
                s = fn(va, vb)
                if s is None:
                    continue
                score_sum += s * w
                weight_sum += w
            if weight_sum <= 0:
                continue
            combined = score_sum / total_weight
            if combined >= threshold:
                out.append((pair_key[0], pair_key[1], float(combined)))
    return out


def _assert_byte_identical(a, b):
    assert len(a) == len(b), f"pair count differs: {len(a)} vs {len(b)}"
    for (ai, aj, asc), (bi, bj, bsc) in zip(a, b):
        assert (ai, aj) == (bi, bj), f"pair/order differs: {(ai, aj)} vs {(bi, bj)}"
        # Byte-identical float, not just close: a 1-ULP gap can flip a pair
        # across the threshold on a different block.
        assert asc == bsc or (math.isnan(asc) and math.isnan(bsc)), (
            f"score differs for {(ai, aj)}: {asc!r} vs {bsc!r}"
        )


@pytest.mark.parametrize("scorer", sorted(_VEC_SUPPORTED))
@pytest.mark.parametrize("threshold", [0.0, 0.5, 0.8, 0.95])
def test_single_field_byte_parity(scorer, threshold):
    n = len(_NAMES)
    row_ids = list(range(100, 100 + n))
    field_arrays = [list(_NAMES)]
    weights = [1.0]
    total_weight = 1.0
    fe = frozenset()
    vec = _score_block_vec(row_ids, field_arrays, [scorer], weights,
                           0, n, total_weight, threshold, fe)
    ref = _ref_per_pair(row_ids, field_arrays, [scorer], weights,
                        0, n, total_weight, threshold, fe)
    _assert_byte_identical(vec, ref)


@pytest.mark.parametrize("scorer", sorted(_VEC_SUPPORTED))
def test_nonpow2_weight_parity(scorer):
    # weight 3.0 makes combined = s*3/3, which is NOT guaranteed == s in float;
    # the lane must reproduce the same (score_sum/total_weight) bits.
    n = len(_NAMES)
    row_ids = list(range(n))
    field_arrays = [list(_NAMES)]
    vec = _score_block_vec(row_ids, field_arrays, [scorer], [3.0],
                           0, n, 3.0, 0.7, frozenset())
    ref = _ref_per_pair(row_ids, field_arrays, [scorer], [3.0],
                        0, n, 3.0, 0.7, frozenset())
    _assert_byte_identical(vec, ref)


def test_multi_field_and_exclusions_parity():
    n = len(_NAMES)
    row_ids = list(range(n))
    suffix = [s[::-1] for s in _NAMES]  # second field, different values
    field_arrays = [list(_NAMES), suffix]
    scorers = ["jaro_winkler", "token_sort"]
    weights = [2.0, 1.0]
    total_weight = 3.0
    # Exclude a couple of canonical pairs to exercise the post-filter.
    fe = frozenset({(0, 1), (2, 5)})
    vec = _score_block_vec(row_ids, field_arrays, scorers, weights,
                           0, n, total_weight, 0.6, fe)
    ref = _ref_per_pair(row_ids, field_arrays, scorers, weights,
                        0, n, total_weight, 0.6, fe)
    _assert_byte_identical(vec, ref)


def test_offset_block_parity():
    # The lane is called with a non-zero offset into the bucket-wide arrays;
    # row_ids[offset:end] indexing must line up with the matrix.
    pad = ["zzz"] * 4
    names = pad + _NAMES
    n = len(names)
    row_ids = list(range(1000, 1000 + n))
    field_arrays = [names]
    vec = _score_block_vec(row_ids, field_arrays, ["jaro_winkler"], [1.0],
                           4, n, 1.0, 0.75, frozenset())
    ref = _ref_per_pair(row_ids, field_arrays, ["jaro_winkler"], [1.0],
                        4, n, 1.0, 0.75, frozenset())
    _assert_byte_identical(vec, ref)
