"""Parity for the ensemble bucket kernel (score-core id 12) vs the pure Python
reference `_ensemble_score_single`.

ensemble = max(jaro_winkler, unscaled token_sort, 0.8*soundex_match), composing
score_one ids 0/2/6. Unlike the integer-popcount bloom scorers (byte-exact), the
jaro_winkler / token_sort components are rapidfuzz-rs (native) vs rapidfuzz-cpp
(Python) -- they agree to ~machine epsilon (observed max|diff| 1.1e-16 over real
Febrl3 name/address pairs), so parity is asserted with a tight FLOAT tolerance,
not `==`. The soundex component is binary 1.0/0.0 (score_one id 6 == the per-pair
jellyfish mirror), so the 0.8 bonus is exact.

A real reimpl bug (wrong token_sort variant / missing scale / wrong bonus) would
blow the 1e-6 tolerance wide open -- the failure mode the ensemble decline warned
about. See docs/superpowers/specs/2026-07-21-ensemble-kernel-measurement.md.
"""
from __future__ import annotations

import random

import pytest
from goldenmatch.core import _native_loader
from goldenmatch.core import scorer as _scorer

_TOL = 1e-6  # float32-level; ~1e10x the observed 1.1e-16, still catches any bug


def _corpus() -> list[tuple[str, str]]:
    rng = random.Random(20260721)
    first = ["John", "Jon", "Jonathan", "Mary", "Marie", "Robert", "Rupert",
             "Bob", "Elizabeth", "Liz", "William", "Bill", "Katherine", "Kate"]
    last = ["Smith", "Smyth", "Smithe", "Jones", "Jonas", "Brown", "Browne",
            "Anderson", "Andersen", "Robinson", "O'Brien", "de la Cruz"]
    pairs: list[tuple[str, str]] = []
    for _ in range(2000):
        a = f"{rng.choice(first)} {rng.choice(last)}"
        b = f"{rng.choice(first)} {rng.choice(last)}"
        pairs.append((a, b))
    # Edges: reordered tokens, case, punctuation, accents, empties, soundex-only.
    pairs += [
        ("John Smith", "Smith John"), ("John SMITH", "smith john"),
        ("Robert", "Rupert"), ("Robert", "Robert"), ("cafe", "café"),
        ("", ""), ("a", "a"), ("abc", "xyz"), ("123", "456"),
        ("O'Brien", "OBrien"), ("Ashcraft", "Ashcroft"),
    ]
    return pairs


def test_ensemble_native_matches_pure_mirror():
    n = _native_loader.native_module()
    if n is None or not hasattr(n, "ensemble_similarity"):
        pytest.skip("native ensemble kernel not built / wheel predates ensemble_similarity")
    for a, b in _corpus():
        got = n.ensemble_similarity(a, b)
        want = _scorer._ensemble_score_single(a, b)
        assert got == pytest.approx(want, abs=_TOL), f"ensemble {a!r} {b!r}: {got} vs {want}"


def test_ensemble_bucket_kernel_id_12_matches_mirror():
    """score_block_pairs dispatching id 12 == the per-pair mirror.

    One ensemble block, weight 1.0, threshold 0.0 so every pair emits.
    """
    n = _native_loader.native_module()
    if n is None or not hasattr(n, "ensemble_similarity"):
        pytest.skip("native ensemble kernel not built")
    values = ["John Smith", "Smith John", "Robert", "Rupert", "Elizabeth", "Liz"]
    row_ids = list(range(len(values)))
    emitted = n.score_block_pairs(
        row_ids, [len(values)], [values], [12], [1.0], 1.0, 0.0, []
    )
    got = {(min(a, b), max(a, b)): s for a, b, s in emitted}
    for i in range(len(values)):
        for j in range(i + 1, len(values)):
            assert got[(i, j)] == pytest.approx(
                _scorer._ensemble_score_single(values[i], values[j]), abs=_TOL
            ), f"id=12 {values[i]!r} {values[j]!r}"


def test_ensemble_native_components():
    """Sanity on the three components: identical -> 1.0, soundex-only floors 0.8."""
    n = _native_loader.native_module()
    if n is None or not hasattr(n, "ensemble_similarity"):
        pytest.skip("native ensemble kernel not built")
    assert n.ensemble_similarity("robert", "robert") == pytest.approx(1.0, abs=_TOL)
    # Robert/Rupert share soundex R163 -> >= 0.8 even with lowish jw/token_sort.
    assert n.ensemble_similarity("Robert", "Rupert") >= 0.8 - _TOL
    # Unrelated -> well below the soundex floor.
    assert n.ensemble_similarity("Robert", "Xyzzy") < 0.8
