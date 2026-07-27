"""Byte-identical parity of the vendored pure-Python strsim primitives vs the
`rapidfuzz` package. This is the proof gate for dropping rapidfuzz from the
scalar scorer sites: each vendored fn matches its SPECIFIC rapidfuzz entry point
bit-for-bit, so the migration carries zero output drift.
"""
from __future__ import annotations

import random
import struct

import pytest
from goldenmatch.core import strsim

rapidfuzz = pytest.importorskip("rapidfuzz")
from rapidfuzz.distance import (  # noqa: E402
    DamerauLevenshtein,
    Indel,
    JaroWinkler,
    Levenshtein,
)
from rapidfuzz.fuzz import partial_ratio as rf_partial_ratio  # noqa: E402
from rapidfuzz.fuzz import ratio as rf_ratio  # noqa: E402
from rapidfuzz.fuzz import token_sort_ratio as rf_token_sort_ratio  # noqa: E402

# Realistic column-name corpus for partial_ratio (the only consumer,
# core/schema_match.py, sees column names — not adversarial strings).
_COLUMN_NAMES = [
    "first_name", "last_name", "surname", "fname", "lname", "full_name", "name",
    "customer_id", "cust_id", "id", "email", "email_address", "phone",
    "phone_number", "telephone", "addr", "address", "street", "city", "zip",
    "zipcode", "postal_code", "dob", "date_of_birth", "birth_date", "birthdate",
    "ssn", "company", "company_name", "org", "organization", "account_number",
    "acct_no", "created_at", "updated_at", "user_id", "uid",
]

# Small alphabet forces frequent matches / transpositions / repeats -- the exact
# regime where jaro transposition order and DL transposition edges bite.
_ALPHABET = "abcdeé12 -"


def _rs(rng: random.Random) -> str:
    return "".join(rng.choice(_ALPHABET) for _ in range(rng.randint(0, 15)))


def _bits(x: float) -> bytes:
    return struct.pack("<d", x)


_EDGE = [
    ("", ""), ("a", ""), ("", "a"), ("a", "a"), ("a", "b"),
    ("ab", "ba"), ("abc", "acb"), ("martha", "marhta"),
    ("dwayne", "duane"), ("dixon", "dicksonx"), ("aabbcc", "abcabc"),
    ("2026-07-26", "2026-07-25"), ("caaba", "aabac"),
]


def _pairs():
    yield from _EDGE
    rng = random.Random(0xC0FFEE)
    for _ in range(30000):
        yield _rs(rng), _rs(rng)


def test_jaro_winkler_similarity_raw_byte_identical():
    for a, b in _pairs():
        assert _bits(strsim.jaro_winkler_similarity(a, b)) == _bits(
            JaroWinkler.similarity(a, b)
        ), (a, b)


def test_jaro_winkler_normalized_byte_identical():
    for a, b in _pairs():
        assert _bits(strsim.jaro_winkler_normalized_similarity(a, b)) == _bits(
            JaroWinkler.normalized_similarity(a, b)
        ), (a, b)


def test_levenshtein_normalized_byte_identical():
    for a, b in _pairs():
        assert _bits(strsim.levenshtein_normalized_similarity(a, b)) == _bits(
            Levenshtein.normalized_similarity(a, b)
        ), (a, b)


def test_indel_normalized_byte_identical():
    for a, b in _pairs():
        assert _bits(strsim.indel_normalized_similarity(a, b)) == _bits(
            Indel.normalized_similarity(a, b)
        ), (a, b)


def test_damerau_levenshtein_distance_identical():
    for a, b in _pairs():
        assert strsim.damerau_levenshtein_distance(a, b) == DamerauLevenshtein.distance(
            a, b
        ), (a, b)


def test_ratio_byte_identical():
    for a, b in _pairs():
        assert _bits(strsim.ratio(a, b)) == _bits(rf_ratio(a, b)), (a, b)


def test_partial_ratio_exact_on_containment_and_faithful_elsewhere():
    # partial_ratio is a FAITHFUL approximation of rapidfuzz's local-alignment
    # partial_ratio (see strsim docstring). Its consumer (schema_match) keys on
    # substring containment, where it must be EXACT; elsewhere it stays close.
    total_err = 0.0
    n = 0
    for a in _COLUMN_NAMES:
        for b in _COLUMN_NAMES:
            got = strsim.partial_ratio(a, b)
            ref = rf_partial_ratio(a, b)
            if a in b or b in a:  # containment -> exact 100, byte-identical
                assert _bits(got) == _bits(ref), ("containment", a, b)
            total_err += abs(got - ref)
            n += 1
    assert total_err / n < 0.5, f"mean abs error {total_err / n} too high"
    # empty-string edges (rapidfuzz: "","" -> 100; one empty -> 0)
    assert strsim.partial_ratio("", "") == rf_partial_ratio("", "")
    assert strsim.partial_ratio("x", "") == rf_partial_ratio("x", "")


def test_token_sort_ratio_byte_identical():
    # multi-token pairs (the regime token_sort actually reorders)
    rng = random.Random(0xABCDEF)

    def _rs_tokens() -> str:
        return " ".join(_rs(rng) for _ in range(rng.randint(0, 4)))

    cases = list(_EDGE) + [
        ("hello world", "world hello"), ("a b c", "c b a"),
        ("New York", "york new"),
    ]
    for _ in range(20000):
        cases.append((_rs_tokens(), _rs_tokens()))
    for a, b in cases:
        assert _bits(strsim.token_sort_ratio(a, b)) == _bits(
            rf_token_sort_ratio(a, b)
        ), (a, b)
