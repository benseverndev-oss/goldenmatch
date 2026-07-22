"""Parity for the soundex_match kernel (score-core id 6) vs the pure-Python
`canonical_soundex` reference.

soundex is kernel-backed on both the bucket path (`score_one` id 6) and the
field-matrix path (native id 4). The canonical in-house kernel is a Unicode-folding
STANDARD Soundex: `NFKD` + uppercase, then walk the string -- ASCII `[A-Z]` code as
classic Soundex, every other char (digit / punctuation / whitespace / combining
mark / exotic letter) is a SEPARATOR that breaks the coding run but never seeds, and
a value with no surviving letter -> `""`. On pure ASCII this equals jellyfish EXCEPT
jellyfish seeds a leading digit; it folds accents jellyfish drops (`Muñoz` -> `M520`).
The `soundex_match` scorer adds an empty-code guard (garbage/empty never matches, not
even another empty code -- so placeholder columns can't mega-cluster).

This batteries native `soundex_similarity(a, b)` (== `score_one(6)`) against the
pure-Python mirror `_soundex_score_single` (which uses `canonical_soundex` + the
same empty guard) over thousands of pairs, INCLUDING the inputs where the kernel
now diverges from jellyfish on purpose: garbage (`123`, `!!`, ``), exotic
non-decomposable letters (`Þór`, `Æthel`), and accented consonants (`Muñoz`).
The exact per-string codes are pinned in score-core's cargo tests.
"""
from __future__ import annotations

import random

import pytest
from goldenmatch.core import _native_loader
from goldenmatch.core.scorer import _soundex_score_single

# The pure-Python reference the native kernel must reproduce byte-for-byte.
_mirror = _soundex_score_single


# Adversarial vocabulary: alpha names (incl. H/W rule + collisions), garbage
# (leading/embedded/only non-alpha), empty, accented Latin, and exotic
# non-decomposable letters.
_VOCAB = [
    "Robert", "Rupert", "Ashcraft", "Ashcroft", "Tymczak", "Pfister", "Honeyman",
    "Smith", "Smyth", "Jackson", "Gutierrez", "Catherine", "Katherine", "Lloyd",
    "Lee", "OBrien", "O'Brien", "McDonald", "MacDonald", "van der Berg",
    "123", "3M", "4abc", "1B2", "99", "S1S", "AB-BA", "-x", "x-y", "A1B", " Robert",
    "", "a", "AA", "H", "W", "HW", "WH", "!!", "000", "---",
    "José", "Ürüm", "naïve", "Zoë", "ß", "Œuvre", "Åke", "Muñoz", "Håkan", "Ægir",
    "Þór", "Æthel", "Đặng", "Ñoño",
]


def _corpus() -> list[tuple[str, str]]:
    rng = random.Random(20260722)
    alpha = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJ"
    mixed = alpha + "0123456789 -'.éüößÅñÞÆ"
    def rand(pool: str) -> str:
        return "".join(rng.choice(pool) for _ in range(rng.randint(0, 12)))
    pairs = [(x, y) for x in _VOCAB for y in _VOCAB]  # every ordered pair incl self
    pairs += [(rand(alpha), rand(alpha)) for _ in range(1500)]
    pairs += [(rand(mixed), rand(mixed)) for _ in range(1500)]
    return pairs


def test_soundex_native_matches_canonical_mirror():
    n = _native_loader.native_module()
    if n is None or not hasattr(n, "soundex_similarity"):
        pytest.skip("native soundex kernel not built / wheel predates soundex_similarity")
    for a, b in _corpus():
        assert n.soundex_similarity(a, b) == _mirror(a, b), f"soundex {a!r} {b!r}"


def test_soundex_empty_guard_and_folding():
    # The cases the canonical spec changed vs jellyfish; now byte-identical native<->pure.
    n = _native_loader.native_module()
    if n is None or not hasattr(n, "soundex_similarity"):
        pytest.skip("native soundex kernel not built")
    # Garbage -> "" -> never matches, not even another garbage value.
    assert n.soundex_similarity("123", "456") == _mirror("123", "456") == 0.0
    assert n.soundex_similarity("123", "123") == 0.0  # identical garbage -> no match
    assert n.soundex_similarity("000", "---") == 0.0
    # Real names collide / fold as expected.
    assert n.soundex_similarity("Robert", "Rupert") == 1.0
    assert n.soundex_similarity("Muñoz", "Munoz") == 1.0  # ñ folds to n
    assert n.soundex_similarity("José", "Jose") == 1.0


def test_soundex_bucket_kernel_id6_matches_mirror():
    """score_block_pairs dispatching scorer id 6 == the canonical per-pair mirror.

    One block, one soundex_match field, weight 1.0, threshold 0.0 so every pair
    emits; the kernel's per-pair score must equal the mirror.
    """
    n = _native_loader.native_module()
    if n is None or not hasattr(n, "soundex_similarity"):
        pytest.skip("native soundex kernel not built")

    values = ["Robert", "Rupert", "Smith", "Smyth", "3M", "José", "123", "Muñoz"]
    row_ids = list(range(len(values)))
    sizes = [len(values)]
    field_values = [values]
    ids = [6]                          # soundex_match
    weights = [1.0]
    total_weight = 1.0
    threshold = 0.0
    emitted = n.score_block_pairs(
        row_ids, sizes, field_values, ids, weights, total_weight, threshold, []
    )
    got = {(min(a, b), max(a, b)): s for a, b, s in emitted}
    for i in range(len(values)):
        for j in range(i + 1, len(values)):
            expected = _mirror(values[i], values[j])
            if expected >= threshold:
                assert got[(i, j)] == expected, f"{values[i]!r} {values[j]!r}"
