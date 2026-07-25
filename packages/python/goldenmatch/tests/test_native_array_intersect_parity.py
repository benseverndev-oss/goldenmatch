"""Native byte-parity for the `array_intersect` domain comparator (P5 of spec
2026-07-25-splink-domain-comparator-conversion). Mirrors the
`test_native_date_diff_geo_parity.py` template: the score-core Rust kernel is the
REFERENCE; the pure-Python `_array_intersect_similarity_py` mirror must be
byte-identical to it (per mode), and the bucket `score_block_pairs` dispatch of
ids 19/20 must equal the per-pair mirror.

`array_intersect` carries its mode on the scorer STRING
(`array_intersect[:jaccard|overlap]`), which -- unlike numeric_diff's continuous
band -- the fixed-id score_one(id,a,b) contract CAN convey as two discrete ids
(19 = jaccard, 20 = overlap).

Skips cleanly when the native kernel isn't built or predates the symbols (a stale
wheel is the wheel-skew case the gating site handles by declining to the pure
mirror, so parity is vacuously satisfied there).
"""
from __future__ import annotations

import random

import pytest
from goldenmatch.core import _native_loader
from goldenmatch.core.scorer import _array_intersect_similarity_py

_n = _native_loader.native_module()

_HAVE_ARRAY = _n is not None and hasattr(_n, "array_intersect_jaccard_similarity")


def _corpus() -> list[str]:
    rng = random.Random(20260725)
    fixed = [
        "a|b|c", "b|c|d", "a|b|c", "b|c", "a", "a", "x", "y", "",
        "a;b;c", "c;d", "a, b ,c", "b", "one two", "one two",
        "a|b", "  a  |  b  ", "a|a|b", "a||b", "|a|b|", " ", "z|y|x|w|v",
        "tag1,tag2,tag3", "tag2,tag4", "single",
    ]
    out = list(fixed)
    pool = ["red", "green", "blue", "cyan", "magenta", "yellow", "black", "white"]
    for _ in range(600):
        k = rng.randint(0, 5)
        sep = rng.choice(["|", ";", ","])
        out.append(sep.join(rng.sample(pool, k)) if k else "")
    return out


@pytest.mark.skipif(not _HAVE_ARRAY, reason="native array_intersect not built / stale wheel")
@pytest.mark.parametrize("mode,symbol", [
    ("jaccard", "array_intersect_jaccard_similarity"),
    ("overlap", "array_intersect_overlap_similarity"),
])
def test_native_array_intersect_matches_pure(mode, symbol):
    corpus = _corpus()
    native = getattr(_n, symbol)
    scorer = f"array_intersect:{mode}"
    for a in corpus:
        for b in corpus[:60]:  # 60 x full corpus is a wide enough cross-product
            assert native(a, b) == _array_intersect_similarity_py(a, b, scorer), (mode, a, b)


@pytest.mark.skipif(not _HAVE_ARRAY, reason="native array_intersect not built / stale wheel")
def test_bare_scorer_is_jaccard():
    # `array_intersect` with no mode suffix is jaccard (score_one id 19).
    corpus = _corpus()
    native = _n.array_intersect_jaccard_similarity
    for a in corpus:
        for b in corpus[:40]:
            assert native(a, b) == _array_intersect_similarity_py(a, b, "array_intersect"), (a, b)


@pytest.mark.skipif(
    not _HAVE_ARRAY or not hasattr(_n, "score_block_pairs"),
    reason="native block kernel not built",
)
@pytest.mark.parametrize("scorer_id,scorer", [
    (19, "array_intersect:jaccard"),
    (20, "array_intersect:overlap"),
])
def test_score_block_pairs_dispatches_new_ids(scorer_id, scorer):
    # The block kernel dispatches each id through score_one; the diagonal-free
    # upper triangle it emits must equal the per-pair mirror for that id.
    vals = ["a|b|c", "b|c|d", "a|b|c", "b|c", "x", ""]
    row_ids = list(range(len(vals)))
    sizes = [len(vals)]        # one block holding every row
    field_values = [vals]      # one field
    threshold = 0.0            # every pair emits
    emitted = _n.score_block_pairs(
        row_ids, sizes, field_values, [scorer_id], [1.0], 1.0, threshold, []
    )
    got = {(min(a, b), max(a, b)): s for a, b, s in emitted}
    for i in range(len(vals)):
        for j in range(i + 1, len(vals)):
            expect = _array_intersect_similarity_py(vals[i], vals[j], scorer)
            # one field, weight 1.0, total_weight 1.0 -> emitted score is
            # score_one(id) in f64 with no downcast, bit-identical to the mirror.
            assert got[(i, j)] == expect, (vals[i], vals[j])
