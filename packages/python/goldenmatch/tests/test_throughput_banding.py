"""Tests for recall-target banding + LSH S-curve (#1083)."""
import math
import pytest
from goldenmatch.core.throughput_verify import (
    expected_recall_lsh, select_banding, DEFAULT_SIMILARITY,
)


def test_expected_recall_jaccard_matches_formula():
    b, r, s = 16, 8, 0.8
    assert expected_recall_lsh("jaccard", s, b, r) == pytest.approx(1 - (1 - s**r)**b)


def test_expected_recall_cosine_uses_bit_match_prob():
    b, r, s = 16, 8, 0.85
    p = 1 - math.acos(s) / math.pi
    assert expected_recall_lsh("cosine", s, b, r) == pytest.approx(1 - (1 - p**r)**b)


def test_select_banding_respects_divisor_invariant():
    b, r = select_banding("jaccard", 128, 0.8, 0.95)
    assert b * r == 128 and 128 % b == 0


def test_select_banding_picks_fewest_bands_meeting_target():
    b, r = select_banding("jaccard", 128, 0.8, 0.95)
    assert expected_recall_lsh("jaccard", 0.8, b, r) >= 0.95
    divisors = [d for d in range(1, 128) if 128 % d == 0 and d < b]
    if divisors:
        b_lower = max(divisors)
        assert expected_recall_lsh("jaccard", 0.8, b_lower, 128 // b_lower) < 0.95


def test_default_similarity_per_metric():
    assert DEFAULT_SIMILARITY["jaccard"] == 0.8
    assert DEFAULT_SIMILARITY["cosine"] == 0.85
