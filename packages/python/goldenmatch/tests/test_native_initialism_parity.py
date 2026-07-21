"""Parity for the initialism_match bucket kernel (score-core id 7) vs the pure
Python reference `_initialism_match_single`.

Wave 2 moves `initialism_match` into `score-core` (`derive_initialism` +
`initialism_match`, byte-for-byte with `goldenmatch.core.acronym.derive_initialism`
+ `core.scorer._initialism_match_single`) and wires it into the *bucket* path via
`score_one` id 7, so `initialism_match` becomes kernel-backed in the metric (the
scorer_kernels surface reads the bucket `_NATIVE_SCORER_IDS`).

Unlike the other bucket kernels, id 7 reads a process-global legal-form variant
set that the host installs once via the native `set_legal_form_variants` shim
(`refdata.business.entity_form_variants()`, ~77 lowercase entries). Without it,
id 7 would drop no legal forms and diverge (`Acme Industries LLC` -> `AIL` not
`AI`). Every test here installs the table first, exactly as the fast-path guard
(`_ensure_legal_forms_installed`) does before routing initialism native.

Parity means native `initialism_similarity(a, b)` (and the bucket id-7 dispatch)
is bit-identical to `_initialism_match_single(a, b)` over adversarial input:
acronyms, multi-word company names, legal-form suffixes, parentheticals,
punctuation-only tokens, single tokens, and Unicode.
"""
from __future__ import annotations

import random

import pytest
from goldenmatch.core import _native_loader
from goldenmatch.core.scorer import _initialism_match_single
from goldenmatch.refdata.business import entity_form_variants


def _install(n) -> None:
    # Idempotent (OnceLock first-wins); the variant set is deterministic.
    n.set_legal_form_variants(list(entity_form_variants()))


def _mirror(a: str, b: str) -> float:
    return _initialism_match_single(a, b)


# Adversarial vocabulary: acronyms (lone + as own key), full expansions,
# legal-form suffixes (dropped), descriptive tokens (kept), parentheticals,
# punctuation-only tokens, single tokens, digits, and Unicode.
_VOCAB = [
    "IBM", "International Business Machines", "Indian Banana Market",
    "International Business Machines Corporation (Armonk, NY)",
    "Acme Industries LLC", "AI", "Acme Industries Inc", "AII",
    "Apple", "Apricot", "Apple Inc", "GmbH", "AB", "3M Company", "3M",
    "General Motors", "GM", "GM Corp", "a b c", "ABC", "ABCDEFG",
    "The Coca Cola Company", "TCCC", "Coca Cola", "CC",
    "", "   ", "...", "-", "A.B.C.", "U.S.A.", "USA",
    "Société Générale", "SG", "Ærø Bank", "Æ B", "Nestlé S.A.", "Nestlé",
    "Foo (bar) Baz", "FB", "Foo Bar", "Ltd", "Co", "X Y Z Ltd",
]


def _corpus() -> list[tuple[str, str]]:
    rng = random.Random(20260721)
    words = ["International", "Business", "Machines", "Corporation", "LLC",
             "Inc", "Ltd", "GmbH", "Acme", "Industries", "Group", "Holdings",
             "IBM", "AI", "3M", "Co", "The", "Société", "Générale", "Æ"]
    def rand_name() -> str:
        k = rng.randint(0, 4)
        return " ".join(rng.choice(words) for _ in range(k))
    pairs = [(x, y) for x in _VOCAB for y in _VOCAB]  # every ordered pair incl self
    pairs += [(rand_name(), rand_name()) for _ in range(2000)]
    return pairs


def test_initialism_native_matches_pure_mirror():
    n = _native_loader.native_module()
    if n is None or not hasattr(n, "initialism_similarity"):
        pytest.skip("native initialism kernel not built / wheel predates initialism_similarity")
    _install(n)
    for a, b in _corpus():
        assert n.initialism_similarity(a, b) == _mirror(a, b), f"initialism {a!r} {b!r}"


def test_initialism_legal_form_dropping_and_acronym_key():
    # The cases that depend on the installed legal-form table + acronym rules.
    n = _native_loader.native_module()
    if n is None or not hasattr(n, "initialism_similarity"):
        pytest.skip("native initialism kernel not built")
    _install(n)
    # "LLC" dropped -> "Acme Industries" -> "AI" == "AI".
    assert n.initialism_similarity("Acme Industries LLC", "AI") == 1.0
    # Expansion <-> acronym both directions.
    assert n.initialism_similarity("International Business Machines", "IBM") == 1.0
    assert n.initialism_similarity("IBM", "International Business Machines") == 1.0
    # Lone non-acronym -> "" -> never matches another lone non-acronym.
    assert n.initialism_similarity("Apple", "Apricot") == 0.0
    # Same-initials collision is a match by design.
    assert n.initialism_similarity(
        "International Business Machines", "Indian Banana Market"
    ) == 1.0
    # 7-char token exceeds the acronym cap -> "" -> no self-key match.
    assert n.initialism_similarity("ABCDEFG", "ABCDEFG") == _mirror("ABCDEFG", "ABCDEFG")


def test_initialism_bucket_kernel_id7_matches_mirror():
    """score_block_pairs dispatching scorer id 7 == the pure per-pair mirror.

    One block, one initialism_match field, weight 1.0, threshold 0.0 so every
    pair emits; the kernel's per-pair score must equal the mirror.
    """
    n = _native_loader.native_module()
    if n is None or not hasattr(n, "initialism_similarity"):
        pytest.skip("native initialism kernel not built")
    _install(n)

    values = [
        "International Business Machines", "IBM", "Acme Industries LLC", "AI",
        "Apple", "Société Générale",
    ]
    row_ids = list(range(len(values)))
    sizes = [len(values)]
    field_values = [values]
    ids = [7]                          # initialism_match
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
