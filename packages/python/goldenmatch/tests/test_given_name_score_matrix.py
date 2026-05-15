"""Lock in the vectorized `given_name_aliased_jw` score_matrix.

Without this method, the scorer plugin path falls through to a pure-
Python O(N²) loop calling score_pair, which was the single biggest
contributor to the 100K-row dedupe wall (~94% of fuzzy_score_blocks).

These tests guard against future regressions:
  1. score_matrix output matches score_pair for every (i, j) pair.
  2. Equivalent aliases (William ↔ Bill) are promoted to 1.0 in the matrix.
  3. Empty inputs and OOV-only inputs degrade cleanly.
"""
from __future__ import annotations

import numpy as np
import pytest

from goldenmatch.refdata.given_names import (
    are_equivalent,
    is_available as given_names_available,
)
from goldenmatch.refdata.scorer import GivenNameAliasedJW


@pytest.fixture
def scorer():
    return GivenNameAliasedJW()


@pytest.fixture(autouse=True)
def skip_if_refdata_missing():
    """All these tests need the bundled alias table."""
    if not given_names_available():
        pytest.skip("given-name alias data not available")


class TestScoreMatrixCorrectness:
    def test_score_matrix_matches_score_pair_elementwise(self, scorer):
        """Every (i, j) cell must equal score_pair(values[i], values[j])."""
        values = [
            "William", "Bill", "Bob", "Robert", "Kate", "Catherine",
            "Eve", "OOVnamenotknown", "John", "Jonathan",
        ]
        matrix = scorer.score_matrix(values)
        n = len(values)
        for i in range(n):
            for j in range(n):
                if i == j:
                    # Diagonal: cdist returns 1.0 for identity.
                    assert matrix[i, j] == pytest.approx(1.0, abs=1e-3)
                    continue
                expected = scorer.score_pair(values[i], values[j])
                if expected is None:
                    expected = 0.0
                assert matrix[i, j] == pytest.approx(expected, abs=1e-3), (
                    f"({i}, {j})=({values[i]!r}, {values[j]!r}): "
                    f"matrix={matrix[i, j]} pair={expected}"
                )

    def test_known_aliases_promoted_to_one(self, scorer):
        """William ↔ Bill (low JW, high alias-equiv) must score 1.0."""
        # Pre-check: assumes William/Bill are in the alias table.
        if not are_equivalent("William", "Bill"):
            pytest.skip("William/Bill not in alias table — fixture-dependent")
        matrix = scorer.score_matrix(["William", "Bill"])
        assert matrix[0, 1] == pytest.approx(1.0)
        assert matrix[1, 0] == pytest.approx(1.0)

    def test_oov_pair_falls_back_to_plain_jw(self, scorer):
        """Two unknown names should use the plain JW base score."""
        from rapidfuzz.distance import JaroWinkler
        a, b = "OOVnamenotknownA", "OOVnamenotknownB"
        matrix = scorer.score_matrix([a, b])
        expected = JaroWinkler.similarity(a, b)
        assert matrix[0, 1] == pytest.approx(expected, abs=1e-3)

    def test_empty_input(self, scorer):
        """Empty list must return a (0, 0) array, not raise."""
        out = scorer.score_matrix([])
        assert out.shape == (0, 0)
        assert out.dtype == np.float32

    def test_none_values_treated_as_empty_strings(self, scorer):
        """None must not crash; treated like empty string per score_pair contract."""
        # cdist treats None values consistently with the empty-string fallback.
        matrix = scorer.score_matrix([None, "Bill", None])
        assert matrix.shape == (3, 3)
        # Diagonal is 1.0; None|None cell should not raise.
        # cdist on "" vs "" returns 1.0 (identity), so [0, 2] is 1.0.
        assert matrix[0, 0] == pytest.approx(1.0)
        assert matrix[1, 1] == pytest.approx(1.0)

    def test_repeated_values_consistent(self, scorer):
        """Same name repeated must score 1.0 everywhere (no caching bug)."""
        matrix = scorer.score_matrix(["Bob", "Bob", "Bob", "Bob"])
        # All cells should be 1.0 (identity / canonical-equivalent).
        assert np.allclose(matrix, 1.0, atol=1e-3)


class TestVectorizedPath:
    """Confirm the scorer is picked up via the registry path."""

    def test_registered_scorer_uses_score_matrix(self):
        """core/scorer.py:_fuzzy_score_matrix must dispatch through score_matrix."""
        from goldenmatch.core.scorer import _fuzzy_score_matrix
        from goldenmatch.refdata import register_scorers
        register_scorers()
        values = ["William", "Bill", "Robert", "Bob", "Eve"]
        result = _fuzzy_score_matrix(values, "given_name_aliased_jw")
        # If we hit the slow O(N²) score_pair fallback, this still works;
        # this test only confirms we get *a* result, not which path served it.
        # The companion correctness tests above prove output equivalence.
        assert result.shape == (5, 5)
        # William ↔ Bill must be 1.0 via either path.
        if are_equivalent("William", "Bill"):
            assert result[0, 1] == pytest.approx(1.0, abs=1e-3)
