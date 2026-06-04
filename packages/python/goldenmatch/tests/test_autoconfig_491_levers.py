"""#491 lever-coverage: real qgram char-n-gram similarity scorer.

Task 0: qgram was only a lossy ``qgram:N`` transform; a genuine
character-n-gram Jaccard *scorer* is needed for short-code routing.
"""

from __future__ import annotations

from goldenmatch.core.scorer import score_field


def test_qgram_scorer_similarity():
    assert score_field("ABC123", "ABC123", "qgram") == 1.0  # identical
    disjoint = score_field("ABC123", "XYZ789", "qgram")
    assert disjoint is not None and disjoint < 0.2  # disjoint
    s = score_field("ABC123", "ABC132", "qgram")
    assert s is not None and 0.3 < s < 1.0  # transposition-ish


def test_qgram_scorer_empty_handling():
    # Both empty -> identical -> 1.0
    assert score_field("", "", "qgram") == 1.0
    # One empty, one not -> no shared grams -> 0.0
    assert score_field("", "ABC123", "qgram") == 0.0


def test_qgram_scorer_matrix_matches_single():
    from goldenmatch.core.scorer import _fuzzy_score_matrix

    vals = ["ABC123", "ABC132", "XYZ789"]
    m = _fuzzy_score_matrix(vals, "qgram")
    n = len(vals)
    assert m.shape == (n, n)
    # Diagonal is self-similarity == 1.0
    for i in range(n):
        assert m[i, i] == 1.0
    # Off-diagonal matches the single-pair scorer
    for i in range(n):
        for j in range(n):
            if i != j:
                single = score_field(vals[i], vals[j], "qgram")
                assert single is not None
                assert abs(m[i, j] - single) < 1e-9
