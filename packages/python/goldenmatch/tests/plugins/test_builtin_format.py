"""Tests for predefined format-canonical plugins (#predefined-merge-plugins).

Spec: docs/superpowers/specs/2026-05-22-predefined-merge-plugins-design.md
"""
from __future__ import annotations

import pytest
from goldenmatch.plugins.builtin.format import (
    ConcatUniqueStrategy,
    EmailNormalizeStrategy,
    PhoneDigitsOnlyStrategy,
    ShortestValueStrategy,
)

# ---------------------------------------------------------------------------
# shortest_value
# ---------------------------------------------------------------------------


def test_shortest_value_picks_shortest():
    val, conf, idx = ShortestValueStrategy().merge(["United States", "US", "USA"])
    assert val == "US"
    assert conf == 1.0
    assert idx == 1


def test_shortest_value_ties_quality_weighted():
    val, conf, idx = ShortestValueStrategy().merge(
        ["AB", "XY"],
        quality_weights=[0.5, 0.9],
    )
    assert val == "XY"
    assert conf == 0.7
    assert idx == 1


def test_shortest_value_ties_no_weights():
    val, conf, idx = ShortestValueStrategy().merge(["AB", "XY"])
    assert val == "AB"
    assert conf == 0.5  # tie, first-wins
    assert idx == 0


def test_shortest_value_all_null():
    val, conf = ShortestValueStrategy().merge([None, None])
    assert val is None
    assert conf == 0.0


# ---------------------------------------------------------------------------
# concat_unique
# ---------------------------------------------------------------------------


def test_concat_unique_joins_sorted():
    val, conf, _ = ConcatUniqueStrategy().merge(
        ["python", "rust", "python", "go"],
    )
    assert val == "go, python, rust"
    assert conf == 1.0


def test_concat_unique_custom_separator():
    val, _, _ = ConcatUniqueStrategy().merge(
        ["a", "b"], rule_kwargs={"separator": " | "},
    )
    assert val == "a | b"


def test_concat_unique_skips_empty_strings():
    val, _, _ = ConcatUniqueStrategy().merge(["", "a", None, "b", ""])
    assert val == "a, b"


def test_concat_unique_all_null():
    val, conf = ConcatUniqueStrategy().merge([None, None, ""])
    assert val is None
    assert conf == 0.0


# ---------------------------------------------------------------------------
# email_normalize
# ---------------------------------------------------------------------------


def test_email_normalize_lowercase_and_strip_plus():
    val, _, _ = EmailNormalizeStrategy().merge(["Bob+Work@X.COM"])
    assert val == "bob@x.com"


def test_email_normalize_picks_mode():
    """3 records normalize to bob@x.com; 1 to alice@x.com -> bob wins."""
    val, conf, _ = EmailNormalizeStrategy().merge([
        "bob@x.com", "Bob+News@X.com", "BOB@X.com", "alice@x.com",
    ])
    assert val == "bob@x.com"
    assert conf == 0.75  # 3/4


def test_email_normalize_all_null():
    val, conf = EmailNormalizeStrategy().merge([None, None])
    assert val is None
    assert conf == 0.0


def test_email_normalize_single_value():
    val, conf, idx = EmailNormalizeStrategy().merge(["solo@example.com"])
    assert val == "solo@example.com"
    assert conf == 1.0
    assert idx == 0


# ---------------------------------------------------------------------------
# phone_digits_only
# ---------------------------------------------------------------------------


def test_phone_digits_only_strips_formatting():
    val, _, _ = PhoneDigitsOnlyStrategy().merge(["(555) 123-4567"])
    assert val == "5551234567"


def test_phone_digits_only_prefers_most_digits():
    """+1 555 123 4567 (11 digits) wins over 555-123-4567 (10)."""
    val, conf, idx = PhoneDigitsOnlyStrategy().merge([
        "555-123-4567", "+1 555 123 4567", "5551234",
    ])
    assert val == "15551234567"
    assert idx == 1
    assert conf == 1.0


def test_phone_digits_only_all_null():
    val, conf = PhoneDigitsOnlyStrategy().merge([None, None])
    assert val is None
    assert conf == 0.0


def test_phone_digits_only_all_non_digit():
    val, conf = PhoneDigitsOnlyStrategy().merge(["abc", "xyz"])
    assert val is None
    assert conf == 0.0


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
