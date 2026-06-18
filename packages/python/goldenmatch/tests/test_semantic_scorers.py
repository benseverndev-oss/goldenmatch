"""Tests for the two free deterministic equality scorers:

- ``initialism_match`` — 1.0 if one string's initialism equals the other
  string, or their initialisms are equal (empty-guarded).
- ``alias_match`` — 1.0 if both canonicalize to the same (non-empty) business
  alias OR the same given-name canonical.

Both are equality-style (1.0/0.0) scorers, mirroring ``soundex_match``. The
matrix-parity tests assert the block path (``_fuzzy_score_matrix``) agrees
cell-for-cell with the pairwise ``score_field`` path.
"""
from __future__ import annotations

import numpy as np
from goldenmatch.core.scorer import _fuzzy_score_matrix, score_field


def test_initialism_match():
    assert score_field("IBM", "International Business Machines", "initialism_match") == 1.0
    assert score_field("International Business Machines", "IBM", "initialism_match") == 1.0
    # Initialism collision — the documented false-positive risk. Assert it holds.
    assert score_field("IBM", "Indian Banana Market", "initialism_match") == 1.0
    assert score_field("IBM", "Apple", "initialism_match") == 0.0
    assert score_field("", "", "initialism_match") == 0.0
    # Both single-token -> derive_initialism returns "" -> empty-guarded to 0.0.
    assert score_field("Apple", "Apricot", "initialism_match") == 0.0


def test_alias_match():
    # Business alias seed table: "Acme Inc" and "Acme Incorporated" both
    # canonicalize to "acme" (confirmed against the bundled data).
    assert score_field("Acme Inc", "Acme Incorporated", "alias_match") == 1.0
    # Given-name table: "Bob" and "Robert" both canonicalize to "robert".
    assert score_field("Bob", "Robert", "alias_match") == 1.0
    assert score_field("Acme", "Globex", "alias_match") == 0.0
    assert score_field("", "", "alias_match") == 0.0


def _pairwise_matrix(values: list[str], scorer: str) -> np.ndarray:
    """Reference NxN matrix built straight from the pairwise score_field."""
    n = len(values)
    out = np.zeros((n, n), dtype=np.float64)
    for i in range(n):
        for j in range(n):
            s = score_field(values[i], values[j], scorer)
            out[i, j] = 0.0 if s is None else float(s)
    return out


def test_initialism_match_matrix_parity():
    values = ["IBM", "International Business Machines", "Indian Banana Market", "Apple"]
    matrix = _fuzzy_score_matrix(values, "initialism_match")
    expected = _pairwise_matrix(values, "initialism_match")
    assert matrix.shape == (len(values), len(values))
    np.testing.assert_array_equal(matrix, expected)


def test_alias_match_matrix_parity():
    values = ["Acme Inc", "Acme Incorporated", "Bob", "Robert"]
    matrix = _fuzzy_score_matrix(values, "alias_match")
    expected = _pairwise_matrix(values, "alias_match")
    assert matrix.shape == (len(values), len(values))
    np.testing.assert_array_equal(matrix, expected)
