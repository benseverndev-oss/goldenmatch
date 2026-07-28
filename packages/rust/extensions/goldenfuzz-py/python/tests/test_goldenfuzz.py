"""goldenfuzz (pyo3 wheel) smoke + byte-identity-vs-rapidfuzz tests.

rapidfuzz is a TEST-only oracle here (not a runtime dep of goldenfuzz); these
assert the wheel is bit-for-bit identical to it on the scalar scorers, and that
the one-vs-many API (extract / cdist / BatchComparator) matches the per-pair
scorers and honours cutoff/limit/order.
"""
from __future__ import annotations

import goldenfuzz as gf
import pytest

rapidfuzz = pytest.importorskip("rapidfuzz")
from rapidfuzz.distance import Indel, JaroWinkler, Levenshtein  # noqa: E402

PAIRS = [
    ("jonathan", "jonathon"),
    ("kitten", "sitting"),
    ("acme corporation", "acme corp"),
    ("", ""),
    ("a", ""),
    ("café münchen", "cafe munchen"),
    ("the quick brown fox " * 30, "the quick brown fox " * 29 + "dog"),
]


@pytest.mark.parametrize("a,b", PAIRS)
def test_byte_identical_to_rapidfuzz(a: str, b: str) -> None:
    assert gf.jaro_winkler(a, b) == JaroWinkler.normalized_similarity(a, b)
    assert gf.levenshtein(a, b) == Levenshtein.normalized_similarity(a, b)
    assert gf.indel(a, b) == Indel.normalized_similarity(a, b)


def test_extract_topk_cutoff_order() -> None:
    choices = ["johnathan smith", "jonathan smith", "jon smith", "jane doe", "j smith"]
    got = gf.extract("jonathan smith", choices, scorer="jaro_winkler", score_cutoff=0.5, limit=3)
    assert len(got) <= 3 and got
    assert got[0][0] == 1  # the exact match ranks first
    assert all(got[i][1] >= got[i + 1][1] for i in range(len(got) - 1))  # descending
    assert all(s >= 0.5 for _, s in got)  # cutoff


def test_batch_matches_per_pair_and_cdist() -> None:
    choices = ["jon smith", "jane doe", "jonathan smith"]
    bc = gf.BatchComparator("jonathan smith")
    for c in choices:
        assert bc.jaro_winkler(c) == gf.jaro_winkler("jonathan smith", c)
        assert bc.score(c, scorer="indel") == gf.indel("jonathan smith", c)
    mat = gf.cdist(["abc", "jon"], choices, scorer="levenshtein")
    for i, q in enumerate(["abc", "jon"]):
        for j, c in enumerate(choices):
            assert mat[i][j] == gf.levenshtein(q, c)


def test_bad_scorer_raises() -> None:
    with pytest.raises(ValueError):
        gf.extract("a", ["b"], scorer="nope")
