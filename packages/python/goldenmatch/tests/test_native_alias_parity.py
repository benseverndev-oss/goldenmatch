"""Parity for the alias_match bucket kernel (score-core id 8) vs the pure Python
reference `_alias_match_single`.

Wave 2 moves `alias_match` into `score-core` (`canonical_company_form` +
`canonical_form` + `_alias_match_single`, byte-for-byte with
`refdata.business_aliases` / `refdata.given_names` / `core.scorer`) and wires it
into the *bucket* path via `score_one` id 8, so `alias_match` becomes
kernel-backed in the metric (the scorer_kernels surface reads the bucket
`_NATIVE_SCORER_IDS`).

Like initialism_match, id 8 reads host-installed tables (business legal-form
variants + surface->canonical map; a pre-resolved given-name
`normalized -> min(canonical)` map). Without them, id 8 canonicalizes nothing and
diverges. Every test installs both tables first, exactly as the fast-path guard
(`_ensure_alias_tables_installed`) does before routing alias_match native.

Parity means native `alias_match_similarity(a, b)` (and the bucket id-8 dispatch)
is bit-identical to `_alias_match_single(a, b)` over adversarial input: business
names with legal-form suffixes (single + compound + multi-word), alias-map
surfaces (Google/Alphabet), given-name nicknames, casing, punctuation, empties.
"""
from __future__ import annotations

import random

import pytest
from goldenmatch.core import _native_loader
from goldenmatch.core.scorer import _alias_match_single


def _install(n) -> bool:
    from goldenmatch.refdata import business as _business
    from goldenmatch.refdata import business_aliases as _ba
    from goldenmatch.refdata import given_names as _gn

    _business._load()
    _ba._load()
    _gn._load()
    if _business._state is None or _ba._state is None or _gn._state is None:
        return False
    n.set_business_aliases(
        list(_business._state.variants_normalized),
        list(_ba._state.surface_to_canonical.items()),
    )
    n.set_given_name_canonicals([(k, min(v)) for k, v in _gn._state.canonicals.items()])
    return True


def _mirror(a: str, b: str) -> float:
    return _alias_match_single(a, b)


# Adversarial vocabulary: business surfaces + legal-form suffixes (single,
# compound, multi-word), alias-map pairs, given-name nicknames, punctuation,
# casing, empties.
_VOCAB = [
    "Acme Inc", "Acme Incorporated", "Acme, LLC", "Acme Holdings Inc.",
    "Acme Limited Liability Company", "Acme", "acme", "ACME CORP",
    "Google", "Alphabet", "Google Inc", "Facebook", "Meta", "Globex", "Initech",
    "Bob", "Robert", "Bill", "William", "Kate", "Catherine", "Bobby", "Will",
    "Xavier", "Zelda", "", "   ", "...", "-", "Co", "Ltd", "GmbH",
    "Jones & Co", "Smith Ltd.", "Nestlé S.A.", "Société Générale",
]


def _corpus() -> list[tuple[str, str]]:
    rng = random.Random(20260721)
    comps = ["Acme", "Google", "Alphabet", "Facebook", "Meta", "Globex", "Initech",
             "Umbrella", "Wayne", "Stark"]
    forms = ["Inc", "Inc.", "LLC", "Ltd", "GmbH", "Corp", "Corporation", "Holdings",
             "Company", "Co", "Limited Liability Company", ""]
    givens = ["Bob", "Robert", "Bill", "William", "Kate", "Catherine", "Bobby",
              "Will", "Kathy", "Xavier", "Zelda", "Rob"]
    def mk() -> str:
        r = rng.random()
        if r < 0.45:
            return (rng.choice(comps) + " " + rng.choice(forms)).strip()
        if r < 0.85:
            return rng.choice(givens)
        return "".join(rng.choice("abcABC .,-") for _ in range(rng.randint(0, 10)))
    pairs = [(x, y) for x in _VOCAB for y in _VOCAB]  # every ordered pair incl self
    pairs += [(mk(), mk()) for _ in range(3000)]
    return pairs


def test_alias_native_matches_pure_mirror():
    n = _native_loader.native_module()
    if n is None or not hasattr(n, "alias_match_similarity"):
        pytest.skip("native alias kernel not built / wheel predates alias_match_similarity")
    if not _install(n):
        pytest.skip("refdata packs unavailable")
    for a, b in _corpus():
        assert n.alias_match_similarity(a, b) == _mirror(a, b), f"alias {a!r} {b!r}"


def test_alias_business_and_given_name_paths():
    n = _native_loader.native_module()
    if n is None or not hasattr(n, "alias_match_similarity"):
        pytest.skip("native alias kernel not built")
    if not _install(n):
        pytest.skip("refdata packs unavailable")
    # Business: legal-form strip (single, iterative compound, multi-word) + alias map.
    assert n.alias_match_similarity("Acme Inc", "Acme Incorporated") == 1.0
    assert n.alias_match_similarity("Acme, LLC", "Acme") == 1.0
    assert n.alias_match_similarity("Acme Holdings Inc.", "acme") == 1.0
    assert n.alias_match_similarity("Acme Limited Liability Company", "Acme") == 1.0
    assert n.alias_match_similarity("Google", "Alphabet") == _mirror("Google", "Alphabet")
    assert n.alias_match_similarity("Acme", "Globex") == 0.0
    # Given-name: nickname canonical equality (+ a non-match).
    assert n.alias_match_similarity("Bob", "Robert") == 1.0
    assert n.alias_match_similarity("Kate", "Catherine") == 1.0
    assert n.alias_match_similarity("Bob", "Bill") == 0.0
    # Empty both -> no canonical -> 0.0.
    assert n.alias_match_similarity("", "") == 0.0


def test_alias_bucket_kernel_id8_matches_mirror():
    """score_block_pairs dispatching scorer id 8 == the pure per-pair mirror.

    One block, one alias_match field, weight 1.0, threshold 0.0 so every pair
    emits; the kernel's per-pair score must equal the mirror.
    """
    n = _native_loader.native_module()
    if n is None or not hasattr(n, "alias_match_similarity"):
        pytest.skip("native alias kernel not built")
    if not _install(n):
        pytest.skip("refdata packs unavailable")

    values = ["Acme Inc", "Acme Incorporated", "Google", "Alphabet", "Bob", "Robert"]
    row_ids = list(range(len(values)))
    sizes = [len(values)]
    field_values = [values]
    ids = [8]                          # alias_match
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
