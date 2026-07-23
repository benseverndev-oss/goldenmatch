"""Native byte-parity for the FS domain comparators `date_diff` / `geo_haversine`
(Phase 3 of spec 2026-07-23-fs-domain-comparators). Mirrors the
`test_native_qgram_parity.py` template: the score-core Rust kernel is the
REFERENCE; the pure-Python `_date_diff_similarity_py` / `_geo_haversine_
similarity_py` mirrors must be byte-identical to it, and the bucket
`score_block_pairs` dispatch of ids 15/16 must equal the per-pair mirror.

Skips cleanly when the native kernel isn't built or predates the symbols (a
stale wheel is the wheel-skew case the gating site handles by declining to the
pure mirror, so parity is vacuously satisfied there).
"""
from __future__ import annotations

import random

import pytest
from goldenmatch.core import _native_loader
from goldenmatch.core.scorer import (
    _date_diff_similarity_py,
    _geo_haversine_similarity_py,
)

_n = _native_loader.native_module()

_HAVE_DATE_DIFF = _n is not None and hasattr(_n, "date_diff_similarity")
_HAVE_GEO = _n is not None and hasattr(_n, "geo_haversine_similarity")


def _date_corpus() -> list[str]:
    rng = random.Random(20260723)
    fixed = [
        "1990-01-02", "1990-01-02", "1991-01-02", "1990-02-01", "1990-01-03",
        "1985", "1985-01-01", "19900102", "1990/01/02", "1990-1-2",
        "1990-13-40", "not a date", "", "2000-02-29", "1999-02-29",
        "1990-06-15", "1990-07-16", "1995-06-15", "Jan 2 1990",
    ]
    out = list(fixed)
    for _ in range(600):
        y = rng.randint(1900, 2010)
        m = rng.randint(1, 12)
        d = rng.randint(1, 28)
        out.append(f"{y:04d}-{m:02d}-{d:02d}")
    return out


def _geo_corpus() -> list[str]:
    rng = random.Random(20260724)
    fixed = [
        "40.0,-74.0", "40.0005,-74.0", "40.0050,-74.0", "40.0500,-74.0",
        "41.0,-74.0", "40.0;-74.0", "34.0522,-118.2437", "unknown", "",
        "91.0,0.0", "0.0,181.0", "40.0", "a,b",
    ]
    out = list(fixed)
    # random coordinates comfortably inside bands (avoid exact band edges so the
    # banded output is robust to any sub-ULP libm difference between Rust/Python)
    for _ in range(600):
        lat = round(rng.uniform(-89.0, 89.0), 4)
        lon = round(rng.uniform(-179.0, 179.0), 4)
        out.append(f"{lat},{lon}")
    return out


@pytest.mark.skipif(not _HAVE_DATE_DIFF, reason="native date_diff not built / stale wheel")
def test_native_date_diff_matches_pure():
    corpus = _date_corpus()
    for a in corpus:
        for b in corpus[:60]:  # 60 x full corpus is a wide enough cross-product
            assert _n.date_diff_similarity(a, b) == _date_diff_similarity_py(a, b), (a, b)


@pytest.mark.skipif(not _HAVE_GEO, reason="native geo_haversine not built / stale wheel")
def test_native_geo_haversine_matches_pure():
    corpus = _geo_corpus()
    for a in corpus:
        for b in corpus[:60]:
            assert _n.geo_haversine_similarity(a, b) == _geo_haversine_similarity_py(a, b), (a, b)


@pytest.mark.skipif(
    not (_HAVE_DATE_DIFF and _HAVE_GEO) or not hasattr(_n, "score_block_pairs"),
    reason="native block kernel not built",
)
@pytest.mark.parametrize("scorer_id,pure", [
    (17, _date_diff_similarity_py),
    (18, _geo_haversine_similarity_py),
])
def test_score_block_pairs_dispatches_new_ids(scorer_id, pure):
    # The block kernel dispatches each id through score_one; the diagonal-free
    # upper triangle it emits must equal the per-pair mirror for that id.
    vals = (
        ["1990-01-02", "1991-01-02", "1990-02-01", "1985", "bad"]
        if scorer_id == 17
        else ["40.0,-74.0", "40.05,-74.0", "41.0,-74.0", "34.05,-118.24", "bad"]
    )
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
            expect = pure(vals[i], vals[j])
            # one field, weight 1.0, total_weight 1.0 -> emitted score is
            # score_one(id) in f64 with no downcast, bit-identical to the mirror.
            assert got[(i, j)] == expect, (vals[i], vals[j])
