"""Tests for AliasScorer."""
from __future__ import annotations

import pytest
from tests.conftest import make_field
from infermap.scorers.alias import AliasScorer


@pytest.fixture
def scorer():
    return AliasScorer()


def test_known_alias_tel_to_phone(scorer):
    """'tel' is an alias of 'phone' — should score high."""
    src = make_field("tel")
    tgt = make_field("phone")
    result = scorer.score(src, tgt)
    assert result is not None
    assert result.score == 0.95


def test_reverse_alias(scorer):
    """'phone' maps to canonical 'phone'; 'mobile' also maps to canonical 'phone'."""
    src = make_field("phone")
    tgt = make_field("mobile")
    result = scorer.score(src, tgt)
    assert result is not None
    assert result.score == 0.95


def test_no_match_returns_none_for_unknown_fields(scorer):
    """Two completely unknown field names — scorer should abstain."""
    src = make_field("frobulate")
    tgt = make_field("zorbix")
    result = scorer.score(src, tgt)
    assert result is None


def test_same_canonical_fname_given_name(scorer):
    """fname → first_name, given_name → first_name: same canonical."""
    src = make_field("fname")
    tgt = make_field("given_name")
    result = scorer.score(src, tgt)
    assert result is not None
    assert result.score == 0.95


def test_different_canonical_fname_lname(scorer):
    """fname → first_name, lname → last_name: different canonicals → 0.0."""
    src = make_field("fname")
    tgt = make_field("lname")
    result = scorer.score(src, tgt)
    assert result is not None
    assert result.score == 0.0


def test_schema_file_aliases(scorer):
    """Target declares aliases in metadata — source name is in that list."""
    src = make_field("telephone")
    tgt = make_field("contact_phone", metadata={"aliases": ["telephone", "mobile_number"]})
    result = scorer.score(src, tgt)
    assert result is not None
    assert result.score == 0.95


def test_schema_file_aliases_no_match(scorer):
    """Source name is NOT in declared aliases → score 0.0 (not None, because target has aliases)."""
    src = make_field("frobulate")
    tgt = make_field("contact_phone", metadata={"aliases": ["telephone", "mobile_number"]})
    result = scorer.score(src, tgt)
    # tgt has declared aliases but src doesn't match them, and src has no canonical
    # scorer should return a result (not None) with score 0.0
    assert result is not None
    assert result.score == 0.0


def test_name_attribute(scorer):
    assert scorer.name == "AliasScorer"


def test_weight_attribute(scorer):
    assert scorer.weight == 0.95
