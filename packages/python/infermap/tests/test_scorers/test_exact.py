"""Tests for ExactScorer."""
from __future__ import annotations

import pytest
from tests.conftest import make_field
from infermap.scorers.exact import ExactScorer


@pytest.fixture
def scorer():
    return ExactScorer()


def test_exact_match(scorer):
    src = make_field("email")
    tgt = make_field("email")
    result = scorer.score(src, tgt)
    assert result is not None
    assert result.score == 1.0


def test_case_insensitive_match(scorer):
    src = make_field("Email")
    tgt = make_field("EMAIL")
    result = scorer.score(src, tgt)
    assert result.score == 1.0


def test_no_match(scorer):
    src = make_field("first_name")
    tgt = make_field("last_name")
    result = scorer.score(src, tgt)
    assert result.score == 0.0


def test_strips_whitespace(scorer):
    src = make_field("  email  ")
    tgt = make_field("email")
    result = scorer.score(src, tgt)
    assert result.score == 1.0


def test_strips_whitespace_both(scorer):
    src = make_field("  email  ")
    tgt = make_field("  email  ")
    result = scorer.score(src, tgt)
    assert result.score == 1.0


def test_name_attribute(scorer):
    assert scorer.name == "ExactScorer"


def test_weight_attribute(scorer):
    assert scorer.weight == 1.0
