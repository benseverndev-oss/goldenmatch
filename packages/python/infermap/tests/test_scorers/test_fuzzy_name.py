"""Tests for FuzzyNameScorer."""
from __future__ import annotations

import pytest
from tests.conftest import make_field
from infermap.scorers.fuzzy_name import FuzzyNameScorer


@pytest.fixture
def scorer():
    return FuzzyNameScorer()


def test_similar_names_score_high(scorer):
    """first_name vs firstname should be > 0.7."""
    src = make_field("first_name")
    tgt = make_field("firstname")
    result = scorer.score(src, tgt)
    assert result is not None
    assert result.score > 0.7


def test_dissimilar_names_score_low(scorer):
    """Completely unrelated names should score < 0.5."""
    src = make_field("email")
    tgt = make_field("zipcode")
    result = scorer.score(src, tgt)
    assert result is not None
    assert result.score < 0.5


def test_identical_names_score_one(scorer):
    """Identical normalized names should score 1.0."""
    src = make_field("email")
    tgt = make_field("email")
    result = scorer.score(src, tgt)
    assert result is not None
    assert result.score == pytest.approx(1.0)


def test_normalizes_underscores(scorer):
    """zip_code vs zipcode — underscores stripped, should score > 0.8."""
    src = make_field("zip_code")
    tgt = make_field("zipcode")
    result = scorer.score(src, tgt)
    assert result is not None
    assert result.score > 0.8


def test_normalizes_hyphens(scorer):
    """e-mail vs email — hyphens stripped."""
    src = make_field("e-mail")
    tgt = make_field("email")
    result = scorer.score(src, tgt)
    assert result is not None
    assert result.score > 0.8


def test_normalizes_case(scorer):
    """FIRSTNAME vs firstname — case normalized."""
    src = make_field("FIRSTNAME")
    tgt = make_field("firstname")
    result = scorer.score(src, tgt)
    assert result is not None
    assert result.score == pytest.approx(1.0)


def test_normalizes_spaces(scorer):
    """'first name' vs 'firstname' — spaces stripped."""
    src = make_field("first name")
    tgt = make_field("firstname")
    result = scorer.score(src, tgt)
    assert result is not None
    assert result.score > 0.8


def test_name_attribute(scorer):
    assert scorer.name == "FuzzyNameScorer"


def test_weight_attribute(scorer):
    assert scorer.weight == 0.4
