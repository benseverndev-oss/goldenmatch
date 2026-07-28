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


FUZZ_PAIRS = [
    ("this is a test", "this is a test!"),
    ("fuzzy wuzzy was a bear", "wuzzy fuzzy was a bear"),
    ("fuzzy was a bear", "fuzzy fuzzy was a bear"),
    ("fuzzy was a bear but not a dog", "fuzzy was a bear but not a cat"),
    ("a certain string", "cetain"),
    ("jonathan smith", "jon smith"),
    ("mary anne smith", "smith mary anne"),
    ("café münchen", "munchen cafe"),
    ("", ""),
    ("abc", ""),
    ("abcdefghij", "aaaaaabcdefghijkkkkkk"),
]

FUZZ_FNS = [
    "ratio",
    "partial_ratio",
    "token_sort_ratio",
    "token_set_ratio",
    "token_ratio",
    "partial_token_sort_ratio",
    "partial_token_set_ratio",
    "partial_token_ratio",
    "WRatio",
    "QRatio",
]


@pytest.mark.parametrize("a,b", FUZZ_PAIRS)
def test_fuzz_family_byte_identical_to_rapidfuzz(a: str, b: str) -> None:
    from rapidfuzz import fuzz

    for name in FUZZ_FNS:
        theirs = getattr(fuzz, name)(a, b)
        mine = getattr(gf, name)(a, b)
        assert mine == theirs, f"{name}({a!r},{b!r}): goldenfuzz={mine} rapidfuzz={theirs}"
    # snake_case aliases match the rapidfuzz spellings.
    assert gf.w_ratio(a, b) == gf.WRatio(a, b)
    assert gf.q_ratio(a, b) == gf.QRatio(a, b)


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
