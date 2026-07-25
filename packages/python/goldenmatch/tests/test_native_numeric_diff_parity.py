"""Native byte-parity for the `numeric_diff` FS domain comparator (Part D of the
compute-closure arc). Mirrors `test_native_array_intersect_parity.py`: the
score-core Rust kernel is the REFERENCE; the pure-Python
`_numeric_diff_similarity_py` mirror must be byte-identical to it, and the bucket
`score_block_pairs` dispatch of id 22 (the pct:0.1 DEFAULT) must equal the mirror.

The band/mode ride the scorer string (`numeric_diff:abs:<eps>` / `:pct:<frac>`),
which the fixed-id score_one(22) can't carry -- so the PARAMETERIZED forms are
kernel-backed via the spec-carrying scalar shim
(`numeric_diff_similarity(a, b, spec)`), asserted here across specs; the batch
id 22 covers only the default.

Skips cleanly when the native kernel isn't built or predates the symbol.
"""
from __future__ import annotations

import random

import pytest
from goldenmatch.core import _native_loader
from goldenmatch.core.scorer import _numeric_diff_similarity_py

_n = _native_loader.native_module()
_HAVE_NUMERIC_DIFF = _n is not None and hasattr(_n, "numeric_diff_similarity")

_SPECS = [
    "numeric_diff",
    "numeric_diff:pct:0.1",
    "numeric_diff:pct:0.5",
    "numeric_diff:abs:2",
    "numeric_diff:abs:100",
    "numeric_diff:bogus",  # malformed -> default pct:0.1
]


def _corpus() -> list[str]:
    rng = random.Random(20260725)
    fixed = ["0", "100", "105", "900", "-5", "10", "11", "13", "1.5e2", "150",
             "0.001", "1000000", "abc", "", "  42  ", "nan", "inf"]
    out = list(fixed)
    for _ in range(300):
        out.append(str(round(rng.uniform(-1000.0, 1000.0), 3)))
    return out


@pytest.mark.skipif(not _HAVE_NUMERIC_DIFF, reason="native numeric_diff not built / stale wheel")
def test_native_numeric_diff_matches_pure_across_specs():
    corpus = _corpus()
    for spec in _SPECS:
        for a in corpus:
            for b in corpus[:40]:
                assert _n.numeric_diff_similarity(a, b, spec) == _numeric_diff_similarity_py(a, b, spec), (spec, a, b)


@pytest.mark.skipif(
    not _HAVE_NUMERIC_DIFF or not hasattr(_n, "score_block_pairs"),
    reason="native block kernel not built",
)
def test_score_block_pairs_dispatches_numeric_diff_id22_default():
    # id 22 is the pct:0.1 DEFAULT; assert the block kernel matches the mirror
    # scored with the bare "numeric_diff" (default) scorer.
    vals = ["100", "105", "900", "0", "abc", "-5"]
    row_ids = list(range(len(vals)))
    emitted = _n.score_block_pairs(row_ids, [len(vals)], [vals], [22], [1.0], 1.0, 0.0, [])
    got = {(min(a, b), max(a, b)): s for a, b, s in emitted}
    for i in range(len(vals)):
        for j in range(i + 1, len(vals)):
            expect = _numeric_diff_similarity_py(vals[i], vals[j], "numeric_diff")
            assert got[(i, j)] == expect, (vals[i], vals[j])
