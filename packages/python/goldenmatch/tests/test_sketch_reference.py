"""Golden-constant tests for the pure-Python sketch reference (sketch.py).

These constants are the cross-language parity oracle: the Rust crate, the native
binding, and the TS port must all reproduce them. They were computed from the
reference algorithm and verified independently.
"""
from __future__ import annotations

import pytest
from goldenmatch.core import sketch

_U64_MAX = (1 << 64) - 1


# ---- Task 1.1: hash primitives ----


def test_base_hash_golden():
    assert sketch.base_hash(b"") == 17665956581633026203
    assert sketch.base_hash(b"a") == 198367012849983736
    assert sketch.base_hash(b"ab") == 11528740771484442951
    assert sketch.base_hash(b"hello world") == 417524495691944273


def test_splitmix64_stream_from_zero():
    state, out = 0, []
    for _ in range(4):
        v, state = sketch.splitmix64(state)
        out.append(v)
    assert out == [
        16294208416658607535,
        7960286522194355700,
        487617019471545679,
        17909611376780542444,
    ]


# ---- Task 1.2: shingling ----


def test_shingle_char_basic():
    sh = sketch.shingle("hello world", "char", 3)
    assert len(sh) == 9
    assert sh == sorted(sh)
    assert len(set(sh)) == len(sh)


def test_shingle_word_ascii_whitespace_only():
    # U+00A0 (non-breaking space) is NOT a separator -> one token, k=1 -> 1 shingle.
    assert len(sketch.shingle("a b", "word", 1)) == 1
    # ASCII tab / newline ARE separators -> two tokens, k=1 -> 2 shingles.
    assert len(sketch.shingle("a\tb", "word", 1)) == 2
    assert len(sketch.shingle("a\nb", "word", 1)) == 2


def test_shingle_short_input_single_shingle():
    assert sketch.shingle("ab", "char", 5) == [sketch.base_hash(b"ab")]
    assert sketch.shingle("x", "word", 3) == [sketch.base_hash(b"x")]


def test_shingle_empty_and_whitespace_only_is_empty_set():
    assert sketch.shingle("", "char", 3) == []
    assert sketch.shingle("   \t\n", "word", 2) == []  # zero tokens take precedence


def test_shingle_unknown_mode_raises():
    with pytest.raises(ValueError):
        sketch.shingle("x", "bigram", 2)


# ---- Task 1.3: signature + jaccard ----


def test_signature_golden():
    sh = sketch.shingle("hello world", "char", 3)
    assert sketch.signature(sh, 8, 42) == [
        17041167395646177,
        77277049784527919,
        186077308732231195,
        564709922545612565,
        113913446168519210,
        82732991858855180,
        16713511289126713,
        83663724776489692,
    ]


def test_signature_empty_is_all_max():
    assert sketch.signature([], 8, 42) == [_U64_MAX] * 8


def test_estimate_jaccard_matches_true_within_tolerance():
    import random

    rng = random.Random(1)
    words = [str(rng.randint(0, 500)) for _ in range(60)]
    a = " ".join(words)
    b = " ".join(w for w in words if rng.random() > 0.3)
    sa = sketch.shingle(a, "word", 2)
    sb = sketch.shingle(b, "word", 2)
    est = sketch.estimate_jaccard(sketch.signature(sa, 128, 7), sketch.signature(sb, 128, 7))
    true = len(set(sa) & set(sb)) / len(set(sa) | set(sb))
    assert abs(est - true) < 0.15


# ---- Task 1.4: band hashes + optimal bands ----


def test_band_hashes_golden():
    sig = sketch.signature(sketch.shingle("hello world", "char", 3), 8, 42)
    assert sketch.band_hashes(sig, 4) == [
        12901963457859849374,
        4306753959614852008,
        8435817867480225113,
        7834504510243305493,
    ]


def test_band_hashes_requires_divisible():
    with pytest.raises(ValueError):
        sketch.band_hashes([0] * 8, 3)


def test_optimal_bands_golden():
    assert sketch.optimal_bands(128, 0.5) == (32, 4)
    assert sketch.optimal_bands(128, 0.8) == (8, 16)
    assert sketch.optimal_bands(128, 0.9) == (4, 32)


# ---- Task 1.5: end-to-end + batch ----


def test_sketch_band_hashes_end_to_end():
    bh = sketch.sketch_band_hashes("hello world", mode="char", k=3, num_perms=8, num_bands=4, seed=42)
    assert bh == [
        12901963457859849374,
        4306753959614852008,
        8435817867480225113,
        7834504510243305493,
    ]


def test_band_hashes_batch_matches_singles():
    texts = ["hello world", "", "foo bar baz"]
    batch = sketch.band_hashes_batch(texts, mode="word", k=2, num_perms=16, num_bands=8, seed=3)
    singles = [
        sketch.sketch_band_hashes(t, mode="word", k=2, num_perms=16, num_bands=8, seed=3)
        for t in texts
    ]
    assert batch == singles


def test_signature_batch_matches_singles():
    texts = ["hello world", "", "foo bar baz"]
    batch = sketch.signature_batch(texts, mode="char", k=3, num_perms=16, seed=5)
    singles = [sketch.signature(sketch.shingle(t, "char", 3), 16, 5) for t in texts]
    assert batch == singles
