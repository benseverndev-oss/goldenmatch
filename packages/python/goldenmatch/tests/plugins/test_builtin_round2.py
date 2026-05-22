"""Tests for the round-2 predefined plugins (#predefined-plugins-round-2).

Covers the 12 plugins added in round 2:
- Numeric: numeric_median, numeric_sum, numeric_weighted_average
- Format: url_canonical, whitespace_normalize, boolean_normalize
- Business: enum_canonical, regex_validated, weighted_by_recency
- Aggregation: count_distinct, count_non_null, agreement_rate

Spec: docs/superpowers/specs/2026-05-22-predefined-merge-plugins-design.md
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from goldenmatch.plugins.builtin.aggregation import (
    AgreementRateStrategy,
    CountDistinctStrategy,
    CountNonNullStrategy,
)
from goldenmatch.plugins.builtin.business import (
    EnumCanonicalStrategy,
    RegexValidatedStrategy,
    WeightedByRecencyStrategy,
)
from goldenmatch.plugins.builtin.format import (
    BooleanNormalizeStrategy,
    UrlCanonicalStrategy,
    WhitespaceNormalizeStrategy,
)
from goldenmatch.plugins.builtin.numeric import (
    NumericMedianStrategy,
    NumericSumStrategy,
    NumericWeightedAverageStrategy,
)

# ---------------------------------------------------------------------------
# numeric_median
# ---------------------------------------------------------------------------


def test_numeric_median_odd_count_picks_middle():
    val, _conf, idx = NumericMedianStrategy().merge([10, 30, 20])
    assert val == 20
    assert idx == 2  # the original row containing the median value


def test_numeric_median_even_count_picks_lower_middle():
    """For even count, picks lower-of-two-middles to preserve a real idx."""
    val, _conf, _idx = NumericMedianStrategy().merge([10, 20, 30, 40])
    assert val == 20  # lower middle, not 25 (interpolated)


def test_numeric_median_resilient_to_outlier():
    val, _conf, _idx = NumericMedianStrategy().merge([5, 6, 7, 8, 1000])
    assert val == 7  # mean would be 205.2


def test_numeric_median_all_null():
    val, conf = NumericMedianStrategy().merge([None, None])
    assert val is None
    assert conf == 0.0


# ---------------------------------------------------------------------------
# numeric_sum
# ---------------------------------------------------------------------------


def test_numeric_sum_aggregates():
    val, conf, idx = NumericSumStrategy().merge([10, 20, 30])
    assert val == 60
    assert conf == 1.0
    assert idx == 0


def test_numeric_sum_skips_null():
    val, _, _ = NumericSumStrategy().merge([10, None, 30])
    assert val == 40


def test_numeric_sum_all_null():
    val, conf = NumericSumStrategy().merge([None, None])
    assert val is None
    assert conf == 0.0


# ---------------------------------------------------------------------------
# numeric_weighted_average
# ---------------------------------------------------------------------------


def test_weighted_avg_uses_weights():
    val, _conf, _idx = NumericWeightedAverageStrategy().merge(
        [10.0, 100.0],
        quality_weights=[0.9, 0.1],
    )
    # 10*0.9 + 100*0.1 = 19.0; /1.0 = 19.0
    assert val == pytest.approx(19.0)


def test_weighted_avg_falls_back_to_uniform_without_weights():
    val, _conf, _idx = NumericWeightedAverageStrategy().merge([10, 20, 30])
    assert val == pytest.approx(20.0)


def test_weighted_avg_all_null():
    val, conf = NumericWeightedAverageStrategy().merge(
        [None, None], quality_weights=[1.0, 1.0],
    )
    assert val is None
    assert conf == 0.0


# ---------------------------------------------------------------------------
# url_canonical
# ---------------------------------------------------------------------------


def test_url_canonical_lowercases_host_and_upgrades_http():
    val, _conf, _idx = UrlCanonicalStrategy().merge(["http://EXAMPLE.com/path"])
    assert val == "https://example.com/path"


def test_url_canonical_trims_trailing_slash():
    val, _, _ = UrlCanonicalStrategy().merge(["https://example.com/"])
    assert val == "https://example.com"


def test_url_canonical_picks_mode():
    val, conf, _ = UrlCanonicalStrategy().merge([
        "https://example.com",
        "HTTPS://Example.COM/",
        "http://example.com",
        "https://other.com",
    ])
    assert val == "https://example.com"
    assert conf == 0.75  # 3 of 4 normalize the same


def test_url_canonical_all_null():
    val, conf = UrlCanonicalStrategy().merge([None, None])
    assert val is None
    assert conf == 0.0


# ---------------------------------------------------------------------------
# whitespace_normalize
# ---------------------------------------------------------------------------


def test_whitespace_collapses_internal_runs():
    val, _, _ = WhitespaceNormalizeStrategy().merge(["Acme    Corp"])
    assert val == "Acme Corp"


def test_whitespace_trims_and_normalizes_tabs():
    val, _, _ = WhitespaceNormalizeStrategy().merge(["  Acme\t\tCorp\n  "])
    assert val == "Acme Corp"


def test_whitespace_picks_mode():
    val, _conf, _idx = WhitespaceNormalizeStrategy().merge([
        "Acme Corp",
        "Acme    Corp ",
        "Other Corp",
    ])
    assert val == "Acme Corp"


def test_whitespace_all_null_or_empty():
    val, conf = WhitespaceNormalizeStrategy().merge([None, "   ", "\t\n"])
    assert val is None
    assert conf == 0.0


# ---------------------------------------------------------------------------
# boolean_normalize
# ---------------------------------------------------------------------------


def test_boolean_normalize_truthy():
    val, _, _ = BooleanNormalizeStrategy().merge(["yes"])
    assert val is True


def test_boolean_normalize_falsy():
    val, _, _ = BooleanNormalizeStrategy().merge(["NO"])
    assert val is False


def test_boolean_normalize_majority_vote():
    val, _conf, _ = BooleanNormalizeStrategy().merge(["Y", "1", "yes", "no"])
    assert val is True
    # 3 truthy vs 1 falsy -> conf 0.75


def test_boolean_normalize_tie_prefers_true():
    val, _, _ = BooleanNormalizeStrategy().merge(["yes", "no"])
    assert val is True


def test_boolean_normalize_unknown_tokens_ignored():
    val, _, _ = BooleanNormalizeStrategy().merge(["maybe", "true"])
    assert val is True


def test_boolean_normalize_all_unknown():
    val, conf = BooleanNormalizeStrategy().merge(["maybe", "perhaps"])
    assert val is None
    assert conf == 0.0


# ---------------------------------------------------------------------------
# enum_canonical
# ---------------------------------------------------------------------------


def test_enum_canonical_maps_aliases():
    val, conf, _ = EnumCanonicalStrategy().merge(
        ["USA", "United States", "U.S."],
        rule_kwargs={
            "alias_map": {
                "USA": "US",
                "United States": "US",
                "U.S.": "US",
            },
        },
    )
    assert val == "US"
    assert conf == 1.0  # all 3 normalize to same


def test_enum_canonical_passes_unknown_through():
    val, _, _ = EnumCanonicalStrategy().merge(
        ["Canada", "Canada", "Mexico"],
        rule_kwargs={"alias_map": {"USA": "US"}},
    )
    assert val == "Canada"  # mode of unmapped values


def test_enum_canonical_case_insensitive_keys():
    val, _, _ = EnumCanonicalStrategy().merge(
        ["usa", "USA", "Usa"],
        rule_kwargs={"alias_map": {"USA": "US"}},
    )
    assert val == "US"


def test_enum_canonical_no_alias_map():
    val, _, _ = EnumCanonicalStrategy().merge(
        ["a", "b", "a"],
    )
    assert val == "a"


# ---------------------------------------------------------------------------
# regex_validated
# ---------------------------------------------------------------------------


def test_regex_validated_filters_matching():
    val, _conf, _ = RegexValidatedStrategy().merge(
        ["not-an-email", "bob@example.com", "alice@example.com"],
        rule_kwargs={"pattern": r"[^@\s]+@[^@\s]+\.[^@\s]+"},
    )
    assert val in {"bob@example.com", "alice@example.com"}


def test_regex_validated_no_match_falls_back():
    val, conf, _ = RegexValidatedStrategy().merge(
        ["junk1", "junk2"],
        rule_kwargs={"pattern": r"[0-9]{4}"},
    )
    assert val == "junk1"
    assert conf == 0.3  # fallback confidence


def test_regex_validated_no_match_null_fallback():
    val, conf = RegexValidatedStrategy().merge(
        ["junk1", "junk2"],
        rule_kwargs={"pattern": r"[0-9]{4}", "fallback": "null"},
    )
    assert val is None
    assert conf == 0.0


def test_regex_validated_no_pattern_first_non_null():
    val, conf, _ = RegexValidatedStrategy().merge(["a", "b"])
    assert val == "a"
    assert conf == 0.5


def test_regex_validated_invalid_pattern_falls_back():
    val, _, _ = RegexValidatedStrategy().merge(
        ["a", "b"], rule_kwargs={"pattern": "["},
    )
    assert val == "a"


# ---------------------------------------------------------------------------
# weighted_by_recency
# ---------------------------------------------------------------------------


def test_weighted_by_recency_prefers_newer():
    now = datetime.now(tz=UTC)
    old = (now - timedelta(days=365)).isoformat()
    new = (now - timedelta(days=1)).isoformat()
    val, _, idx = WeightedByRecencyStrategy().merge(
        ["old-val", "new-val"],
        dates=[old, new],
        rule_kwargs={"half_life_days": 30},
    )
    assert val == "new-val"
    assert idx == 1


def test_weighted_by_recency_no_dates():
    val, conf = WeightedByRecencyStrategy().merge(["a", "b"], dates=None)
    assert val is None
    assert conf == 0.0


def test_weighted_by_recency_skips_missing_dates():
    now = datetime.now(tz=UTC)
    fresh = (now - timedelta(days=1)).isoformat()
    val, _, _ = WeightedByRecencyStrategy().merge(
        ["a", "b", "c"],
        dates=[None, fresh, None],
    )
    assert val == "b"


def test_weighted_by_recency_negative_half_life_uses_default():
    """Bad half_life config falls back to the 30-day default."""
    now = datetime.now(tz=UTC)
    fresh = (now - timedelta(days=1)).isoformat()
    val, _, _ = WeightedByRecencyStrategy().merge(
        ["a"], dates=[fresh], rule_kwargs={"half_life_days": -5},
    )
    assert val == "a"


# ---------------------------------------------------------------------------
# count_distinct
# ---------------------------------------------------------------------------


def test_count_distinct_basic():
    val, conf, _ = CountDistinctStrategy().merge(["a", "b", "a", "c"])
    assert val == 3
    assert conf == 1.0


def test_count_distinct_ignores_null():
    val, _, _ = CountDistinctStrategy().merge(["a", None, "a", "b"])
    assert val == 2


def test_count_distinct_all_null():
    val, conf = CountDistinctStrategy().merge([None, None])
    assert val is None
    assert conf == 0.0


# ---------------------------------------------------------------------------
# count_non_null
# ---------------------------------------------------------------------------


def test_count_non_null_basic():
    val, conf, _ = CountNonNullStrategy().merge(["a", None, "b", None])
    assert val == 2
    assert conf == 1.0


def test_count_non_null_all_null_returns_zero_not_none():
    """Count of zero is well-defined data; don't conflate with None."""
    val, conf, _ = CountNonNullStrategy().merge([None, None])
    assert val == 0
    assert conf == 1.0


# ---------------------------------------------------------------------------
# agreement_rate
# ---------------------------------------------------------------------------


def test_agreement_rate_full_agreement():
    val, _, _ = AgreementRateStrategy().merge(["a", "a", "a"])
    assert val == pytest.approx(1.0)


def test_agreement_rate_split():
    val, _, _ = AgreementRateStrategy().merge(["a", "a", "b", "b"])
    assert val == pytest.approx(0.5)


def test_agreement_rate_coverage_in_confidence():
    """Confidence reflects coverage: 1.0 rate on 1 sample is less robust
    than 1.0 rate on 10 samples. Confidence = non_null / total."""
    val, conf, _ = AgreementRateStrategy().merge(["a", None, None])
    assert val == pytest.approx(1.0)
    assert conf == pytest.approx(1 / 3)


def test_agreement_rate_all_null():
    val, conf = AgreementRateStrategy().merge([None, None])
    assert val is None
    assert conf == 0.0


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
