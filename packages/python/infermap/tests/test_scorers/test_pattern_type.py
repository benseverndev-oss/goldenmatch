"""Tests for PatternTypeScorer and classify_field."""
from __future__ import annotations

import pytest
from tests.conftest import make_field
from infermap.scorers.pattern_type import PatternTypeScorer, classify_field


# ── classify_field ──────────────────────────────────────────────────────────

def test_classify_email():
    field = make_field("f", sample_values=["alice@example.com", "bob@test.org", "carol@mail.net"])
    result = classify_field(field)
    assert result == "email"


def test_classify_phone():
    field = make_field("f", sample_values=["555-867-5309", "555-123-4567", "800-555-1234"])
    result = classify_field(field)
    assert result == "phone"


def test_classify_zip():
    field = make_field("f", sample_values=["90210", "12345", "10001", "94102", "30301"])
    result = classify_field(field)
    assert result == "zip_us"


def test_classify_date_iso():
    field = make_field("f", sample_values=["2024-01-15", "2023-12-01", "2025-06-30"])
    result = classify_field(field)
    assert result == "date_iso"


def test_classify_uuid():
    field = make_field("f", sample_values=[
        "123e4567-e89b-12d3-a456-426614174000",
        "550e8400-e29b-41d4-a716-446655440000",
    ])
    result = classify_field(field)
    assert result == "uuid"


def test_classify_url():
    field = make_field("f", sample_values=[
        "https://example.com",
        "http://test.org/path",
        "https://www.google.com/search?q=foo",
    ])
    result = classify_field(field)
    assert result == "url"


def test_classify_no_match_returns_none():
    field = make_field("f", sample_values=["hello", "world", "foo", "bar"])
    result = classify_field(field)
    assert result is None


def test_classify_empty_samples_returns_none():
    field = make_field("f", sample_values=[])
    result = classify_field(field)
    assert result is None


def test_classify_threshold_respected():
    """Only 1/5 samples match email — below default threshold of 0.6."""
    field = make_field("f", sample_values=[
        "alice@example.com", "hello", "world", "foo", "bar"
    ])
    result = classify_field(field, threshold=0.6)
    assert result is None


def test_classify_low_threshold():
    """1/5 = 0.2 matches email — above threshold=0.2."""
    field = make_field("f", sample_values=[
        "alice@example.com", "hello", "world", "foo", "bar"
    ])
    result = classify_field(field, threshold=0.2)
    assert result == "email"


# ── PatternTypeScorer ────────────────────────────────────────────────────────

@pytest.fixture
def scorer():
    return PatternTypeScorer()


def test_same_type_scores_high(scorer):
    src = make_field("a", sample_values=["alice@x.com", "bob@y.org", "carol@z.net"])
    tgt = make_field("b", sample_values=["dave@p.com", "eve@q.io", "frank@r.co"])
    result = scorer.score(src, tgt)
    assert result is not None
    assert result.score > 0.6


def test_different_types_scores_zero(scorer):
    src = make_field("a", sample_values=["alice@x.com", "bob@y.org", "carol@z.net"])
    tgt = make_field("b", sample_values=["2024-01-01", "2023-06-15", "2025-03-20"])
    result = scorer.score(src, tgt)
    assert result is not None
    assert result.score == 0.0


def test_no_samples_returns_none(scorer):
    src = make_field("a", sample_values=[])
    tgt = make_field("b", sample_values=[])
    result = scorer.score(src, tgt)
    assert result is None


def test_one_side_no_samples_returns_none(scorer):
    src = make_field("a", sample_values=["alice@x.com"])
    tgt = make_field("b", sample_values=[])
    result = scorer.score(src, tgt)
    assert result is None


def test_no_semantic_type_detected(scorer):
    """Samples exist but no semantic type can be classified."""
    src = make_field("a", sample_values=["hello", "world", "foo"])
    tgt = make_field("b", sample_values=["apple", "banana", "cherry"])
    result = scorer.score(src, tgt)
    assert result is not None
    assert result.score == 0.0
    assert "no semantic type" in result.reasoning.lower()


def test_name_attribute(scorer):
    assert scorer.name == "PatternTypeScorer"


def test_weight_attribute(scorer):
    assert scorer.weight == 0.7
