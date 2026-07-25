"""Native byte-parity for the `array_intersect` FS domain comparator (P5 of spec
2026-07-25-splink-domain-comparator-conversion). Mirrors
`test_native_date_diff_geo_parity.py`: the score-core Rust kernel (score_one id
19, jaccard default) is the REFERENCE; the pure-Python `_array_intersect_
similarity_py(a, b, "array_intersect")` mirror must be byte-identical to it, and
the bucket `score_block_pairs` dispatch of id 19 must equal the per-pair mirror.

Only the DEFAULT (jaccard) mode is kernel-backed — `array_intersect:overlap`
rides the scorer string, which the fixed-id score_one(id, a, b) can't carry, so
it declines native (like numeric_diff) and is exercised by the pure-Python /
cross-language TS fixtures instead.

Skips cleanly when the native kernel isn't built or predates the symbol (the
wheel-skew case the gating site handles by declining to the pure mirror).
"""
from __future__ import annotations

import random

import pytest
from goldenmatch.core import _native_loader
from goldenmatch.core.scorer import _array_intersect_similarity_py

_n = _native_loader.native_module()
_HAVE_ARRAY_INTERSECT = _n is not None and hasattr(_n, "array_intersect_similarity")


def _corpus() -> list[str]:
    rng = random.Random(20260725)
    fixed = [
        "a|b|c", "a|b|c", "b|c|d", "a|b", "c|d", "a", "a|b|c|d",
        "x;y;z", "y;z", "p,q,r", "q,r,s", "solo", "other",
        " a | b ", "b|a", "", "a|b|", "|a|b", "a||b", "a|a|b",
    ]
    out = list(fixed)
    toks = ["a", "b", "c", "d", "e", "f", "g", "h"]
    for _ in range(400):
        k = rng.randint(0, 4)
        out.append("|".join(rng.choice(toks) for _ in range(k)))
    return out


@pytest.mark.skipif(
    not _HAVE_ARRAY_INTERSECT, reason="native array_intersect not built / stale wheel"
)
def test_native_array_intersect_matches_pure():
    corpus = _corpus()
    for a in corpus:
        for b in corpus[:60]:
            # The kernel is the jaccard default; the pure mirror scored with the
            # bare "array_intersect" scorer is the same (default) mode.
            assert _n.array_intersect_similarity(a, b) == _array_intersect_similarity_py(
                a, b, "array_intersect"
            ), (a, b)


@pytest.mark.skipif(
    not _HAVE_ARRAY_INTERSECT or not hasattr(_n, "score_block_pairs"),
    reason="native block kernel not built",
)
def test_score_block_pairs_dispatches_array_intersect_id19():
    vals = ["a|b|c", "b|c|d", "a|b", "a", "", "solo"]
    row_ids = list(range(len(vals)))
    sizes = [len(vals)]
    field_values = [vals]
    threshold = 0.0
    emitted = _n.score_block_pairs(row_ids, sizes, field_values, [19], [1.0], 1.0, threshold, [])
    got = {(min(a, b), max(a, b)): s for a, b, s in emitted}
    for i in range(len(vals)):
        for j in range(i + 1, len(vals)):
            expect = _array_intersect_similarity_py(vals[i], vals[j], "array_intersect")
            assert got[(i, j)] == expect, (vals[i], vals[j])
