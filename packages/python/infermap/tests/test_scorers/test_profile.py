"""Tests for ProfileScorer."""
from __future__ import annotations

import pytest
from tests.conftest import make_field
from infermap.scorers.profile import ProfileScorer


@pytest.fixture
def scorer():
    return ProfileScorer()


def test_identical_profiles_score_high(scorer):
    src = make_field(
        "email", dtype="string",
        sample_values=["a@b.com", "c@d.org"],
        null_rate=0.0, unique_rate=1.0, value_count=100,
    )
    tgt = make_field(
        "email_addr", dtype="string",
        sample_values=["x@y.com", "z@w.net"],
        null_rate=0.0, unique_rate=1.0, value_count=100,
    )
    result = scorer.score(src, tgt)
    assert result is not None
    assert result.score >= 0.7


def test_different_dtypes_lower_score(scorer):
    src = make_field(
        "age", dtype="integer",
        sample_values=["25", "30", "45"],
        null_rate=0.0, unique_rate=0.8, value_count=50,
    )
    tgt = make_field(
        "name", dtype="string",
        sample_values=["Alice", "Bob", "Carol"],
        null_rate=0.0, unique_rate=0.8, value_count=50,
    )
    result = scorer.score(src, tgt)
    assert result is not None
    # Dtype mismatch should lower the score relative to identical dtypes
    assert result.score < 0.9


def test_very_different_profiles_low_score(scorer):
    src = make_field(
        "id", dtype="integer",
        sample_values=["1", "2", "3"],
        null_rate=0.0, unique_rate=1.0, value_count=1000,
    )
    tgt = make_field(
        "notes", dtype="string",
        sample_values=["This is a long note about nothing", "Another very lengthy note"],
        null_rate=0.5, unique_rate=0.1, value_count=200,
    )
    result = scorer.score(src, tgt)
    assert result is not None
    assert result.score < 0.5


def test_no_value_count_returns_none(scorer):
    src = make_field("a", value_count=0)
    tgt = make_field("b", value_count=0)
    result = scorer.score(src, tgt)
    assert result is None


def test_one_side_zero_value_count_returns_none(scorer):
    src = make_field("a", value_count=100)
    tgt = make_field("b", value_count=0)
    result = scorer.score(src, tgt)
    assert result is None


def test_name_attribute(scorer):
    assert scorer.name == "ProfileScorer"


def test_weight_attribute(scorer):
    assert scorer.weight == 0.5
