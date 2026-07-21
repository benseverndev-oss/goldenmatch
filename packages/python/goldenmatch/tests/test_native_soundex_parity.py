"""Parity for the soundex_match bucket kernel (score-core id 6) vs jellyfish.

soundex was already a Rust kernel in the *field-matrix* path (native id 4); Wave 1
moves the impl into `score-core` with FULL `jellyfish.soundex` parity (NFKD
normalize + Unicode uppercase + literal-first-char seed) and wires it into the
*bucket* path via `score_one` id 6, so it becomes kernel-backed in the metric
(the scorer_kernels surface reads the bucket `_NATIVE_SCORER_IDS`).

Full parity means native `soundex_similarity(a, b)` is bit-identical to the
bucket per-pair mirror `1.0 if jellyfish.soundex(a) == jellyfish.soundex(b) else
0.0` (`_resolve_score_pair_callable("soundex_match")`) over EVERY input --
including leading non-alpha (`123`, `3M`), embedded symbols (`O'Brien`), and
Unicode (`Ürüm`, `José`, `ß`), where the pre-Wave-1 ASCII-only kernel diverged.
The exact per-string codes are pinned in score-core's cargo tests; this batteries
the equality semantics the kernel actually uses over thousands of pairs.
"""
from __future__ import annotations

import random

import pytest
from goldenmatch.core import _native_loader
from goldenmatch.core import scorer as _scorer_mod

_jf = _scorer_mod.jellyfish


def _mirror(a: str, b: str) -> float:
    # exactly `_resolve_score_pair_callable("soundex_match")`'s lambda
    return 1.0 if _jf.soundex(a) == _jf.soundex(b) else 0.0


# Adversarial vocabulary: alpha names (incl. H/W rule + collisions), leading
# non-alpha, embedded symbols/digits, empty, and Unicode (NFKD + upper edges).
_VOCAB = [
    "Robert", "Rupert", "Ashcraft", "Ashcroft", "Tymczak", "Pfister", "Honeyman",
    "Smith", "Smyth", "Jackson", "Gutierrez", "Catherine", "Katherine", "Lloyd",
    "Lee", "OBrien", "O'Brien", "McDonald", "MacDonald", "van der Berg",
    "123", "3M", "4abc", "1B2", "99", "S1S", "AB-BA", "-x", "x-y", "A1B", " Robert",
    "", "a", "AA", "H", "W", "HW", "WH",
    "José", "Ürüm", "naïve", "Zoë", "ß", "Œuvre", "Åke", "Muñoz", "Håkan", "Ægir",
]


def _corpus() -> list[tuple[str, str]]:
    rng = random.Random(20260721)
    alpha = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJ"
    mixed = alpha + "0123456789 -'.éüößÅ"
    def rand(pool: str) -> str:
        return "".join(rng.choice(pool) for _ in range(rng.randint(0, 12)))
    pairs = [(x, y) for x in _VOCAB for y in _VOCAB]  # every ordered pair incl self
    pairs += [(rand(alpha), rand(alpha)) for _ in range(1500)]
    pairs += [(rand(mixed), rand(mixed)) for _ in range(1500)]
    return pairs


def test_soundex_native_matches_jellyfish_mirror():
    n = _native_loader.native_module()
    if n is None or not hasattr(n, "soundex_similarity"):
        pytest.skip("native soundex kernel not built / wheel predates soundex_similarity")
    for a, b in _corpus():
        assert n.soundex_similarity(a, b) == _mirror(a, b), f"soundex {a!r} {b!r}"


def test_soundex_full_parity_non_alpha_and_unicode():
    # The cases the pre-Wave-1 ASCII-only kernel got wrong; now byte-identical.
    n = _native_loader.native_module()
    if n is None or not hasattr(n, "soundex_similarity"):
        pytest.skip("native soundex kernel not built")
    # 123 -> "1000", 456 -> "4000" (distinct); 123 vs 123 match.
    assert n.soundex_similarity("123", "456") == _mirror("123", "456") == 0.0
    assert n.soundex_similarity("123", "123") == 1.0
    # 3M -> "3500"; Robert/Rupert collide; Ürüm code is stable under NFKD.
    assert n.soundex_similarity("Robert", "Rupert") == 1.0
    assert n.soundex_similarity("Ürüm", "Ürüm") == 1.0


def test_soundex_bucket_kernel_id6_matches_mirror():
    """score_block_pairs dispatching scorer id 6 == the per-pair jellyfish mirror.

    One block, one soundex_match field, weight 1.0, threshold 0.0 so every pair
    emits; the kernel's per-pair score must equal the mirror.
    """
    n = _native_loader.native_module()
    if n is None or not hasattr(n, "soundex_similarity"):
        pytest.skip("native soundex kernel not built")

    values = ["Robert", "Rupert", "Smith", "Smyth", "3M", "José"]
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
