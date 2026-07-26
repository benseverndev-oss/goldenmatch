"""Native byte-parity for the `cosine` vector comparator (score_one id 23; the
single-language-compute-closure arc's final scorer). Mirrors
`test_native_numeric_diff_parity.py`: the score-core Rust kernel is the
REFERENCE; the pure-Python `_cosine_similarity_py` mirror must be byte-identical
to it, and the bucket `score_block_pairs` dispatch of id 23 must equal the mirror.

Cosine has no mode/param, so the fixed id 23 covers it fully (contrast
numeric_diff/array_intersect, whose modes ride the scorer string). Parses two
delimited float-vector columns; unparseable / length-mismatch / zero-norm falls
back to exact-string equality.

Skips cleanly when the native kernel isn't built or predates the symbol.
"""
from __future__ import annotations

import random

import pytest
from goldenmatch.core import _native_loader
from goldenmatch.core.scorer import _cosine_similarity_py

_n = _native_loader.native_module()
_HAVE_COSINE = _n is not None and hasattr(_n, "cosine_similarity")


def _corpus() -> list[str]:
    rng = random.Random(20260726)
    fixed = [
        "1,0,0", "0,1,0", "1,1", "1,0", "-1,0", "2,0,0", "0,0", "0,0,0",
        "0.6,0.8", "[1, 0, 0]", "(0.5 0.5)", "1 0 0", "1,0,0,0",  # len variety
        "x,y", "a,b", "", "  1 , 0 , 0  ", "inf,0", "nan,1", "1e3,0",
    ]
    out = list(fixed)
    for _ in range(80):
        dim = rng.choice([2, 3, 4])
        out.append(",".join(str(round(rng.uniform(-5.0, 5.0), 3)) for _ in range(dim)))
    return out


@pytest.mark.skipif(not _HAVE_COSINE, reason="native cosine not built / stale wheel")
def test_native_cosine_matches_pure():
    corpus = _corpus()
    for a in corpus:
        for b in corpus[:50]:
            assert _n.cosine_similarity(a, b) == _cosine_similarity_py(a, b), (a, b)


@pytest.mark.skipif(
    not _HAVE_COSINE or not hasattr(_n, "score_block_pairs"),
    reason="native block kernel not built",
)
def test_score_block_pairs_dispatches_cosine_id23():
    vals = ["1,0,0", "1,0,0", "0,1,0", "1,1,0", "0,0,0", "x,y"]
    row_ids = list(range(len(vals)))
    emitted = _n.score_block_pairs(row_ids, [len(vals)], [vals], [23], [1.0], 1.0, 0.0, [])
    got = {(min(a, b), max(a, b)): s for a, b, s in emitted}
    for i in range(len(vals)):
        for j in range(i + 1, len(vals)):
            expect = _cosine_similarity_py(vals[i], vals[j])
            assert got[(i, j)] == expect, (vals[i], vals[j])
