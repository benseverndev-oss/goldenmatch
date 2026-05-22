"""Tests for post-cluster golden-rules refinement (#golden-strategies, v1.18).

Spec: docs/superpowers/specs/2026-05-22-intelligent-golden-rules-design.md
"""

from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.config.schemas import GoldenRulesConfig
from goldenmatch.core.golden_rules_refiner import (
    RefinementSignals,
    _pick_strategy_for_field,
    refine_golden_rules,
)


def _signals(**overrides) -> RefinementSignals:
    """Build a RefinementSignals with sensible defaults for one field
    `f`, then apply overrides. Tests can pass `null_rate={"f": 0.6}`
    to set per-field values without spelling out the whole struct.
    """
    defaults = {
        "within_cluster_spread": {"f": 1.0},
        "per_source_completeness": {},
        "per_source_agreement": {},  # v1.18.1
        "date_column_coverage": {},
        "col_type": {"f": "string"},
        "avg_len": {"f": 5.0},
        "null_rate": {"f": 0.0},
    }
    defaults.update(overrides)
    return RefinementSignals(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _pick_strategy_for_field rule table
# ---------------------------------------------------------------------------


def test_picks_most_recent_for_date_column_with_coverage():
    """Date column + > 50% of clusters have all-present dates."""
    s = _signals(
        col_type={"f": "date"},
        date_column_coverage={"f": 0.8},
    )
    result = _pick_strategy_for_field("f", s)
    assert result is not None
    strategy, kwargs = result
    assert strategy == "most_recent"
    assert kwargs == {"date_column": "f"}


def test_skips_most_recent_for_date_column_with_low_coverage():
    """Date column but < 50% coverage -> not picked."""
    s = _signals(
        col_type={"f": "date"},
        date_column_coverage={"f": 0.3},
    )
    result = _pick_strategy_for_field("f", s)
    # Falls through past Rule 1 -> likely returns None (no other rule
    # fires on these signals).
    assert result is None


def test_picks_source_priority_when_one_source_dominates():
    """Three sources, one's completeness is > 1.5x median -> source_priority."""
    s = _signals(
        per_source_completeness={
            "f": {"src_a": 0.95, "src_b": 0.40, "src_c": 0.30},
        },
    )
    result = _pick_strategy_for_field("f", s)
    assert result is not None
    strategy, kwargs = result
    assert strategy == "source_priority"
    # Order by completeness desc: src_a, src_b, src_c
    assert kwargs == {"source_priority": ["src_a", "src_b", "src_c"]}


def test_skips_source_priority_when_no_dominance():
    """Sources are close in completeness -> no clear winner."""
    s = _signals(
        per_source_completeness={
            "f": {"src_a": 0.85, "src_b": 0.80, "src_c": 0.78},
        },
    )
    result = _pick_strategy_for_field("f", s)
    # Top (0.85) / median (0.80) = 1.06 < 1.5 -> no dominance
    # Falls through; default null_rate is 0, spread is 1, col_type=string,
    # avg_len=5 -> no other rule matches.
    assert result is None


def test_picks_longest_value_for_free_text_with_disagreement():
    """col_type=string + avg_len > 20 + within-cluster spread > 1.5."""
    s = _signals(
        col_type={"f": "address"},
        avg_len={"f": 35.0},
        within_cluster_spread={"f": 1.8},
    )
    result = _pick_strategy_for_field("f", s)
    assert result is not None
    strategy, kwargs = result
    assert strategy == "longest_value"
    assert kwargs == {}


def test_picks_first_non_null_for_sparse_column():
    """null_rate > 0.5 -> first_non_null fast path."""
    s = _signals(null_rate={"f": 0.7})
    result = _pick_strategy_for_field("f", s)
    assert result is not None
    strategy, kwargs = result
    assert strategy == "first_non_null"
    assert kwargs == {}


def test_picks_confidence_majority_on_high_spread():
    """spread > 2.0 (high within-cluster disagreement) -> confidence_majority."""
    s = _signals(within_cluster_spread={"f": 2.5})
    result = _pick_strategy_for_field("f", s)
    assert result is not None
    strategy, kwargs = result
    assert strategy == "confidence_majority"
    assert kwargs == {}


# ---------------------------------------------------------------------------
# #smarter-refiner: compliance name + identity + sibling-timestamp rules
# ---------------------------------------------------------------------------


def test_picks_unanimous_or_null_for_compliance_named_field():
    """Compliance-shaped column names ALWAYS get unanimous_or_null,
    regardless of other signals."""
    for compliance_name in ["ssn", "patient_ssn", "npi", "tax_id", "dob",
                             "drivers_license", "passport_number", "mrn"]:
        s_named = _signals(
            col_type={compliance_name: "identifier"},
            null_rate={compliance_name: 0.0},
        )
        result = _pick_strategy_for_field(compliance_name, s_named)
        assert result is not None, f"{compliance_name} did not match compliance pattern"
        strategy, _ = result
        assert strategy == "unanimous_or_null", (
            f"{compliance_name} should get unanimous_or_null; got {strategy}"
        )


def test_picks_unanimous_or_null_for_high_cardinality_identifier():
    """col_type=identifier + cardinality_ratio > 0.9 -> unanimous_or_null."""
    s = _signals(col_type={"f": "identifier"})
    result = _pick_strategy_for_field("f", s, cardinality_ratio=0.95)
    assert result is not None
    strategy, kwargs = result
    assert strategy == "unanimous_or_null"
    assert kwargs == {}


def test_low_cardinality_identifier_does_not_get_unanimous_or_null():
    """col_type=identifier but cardinality < 0.9 (e.g. status code) -> normal flow."""
    s = _signals(col_type={"f": "identifier"})
    result = _pick_strategy_for_field("f", s, cardinality_ratio=0.3)
    # No other rule fires on these neutral signals.
    assert result is None


def test_picks_most_recent_with_sibling_timestamp_for_address():
    """A mutable-shaped field with a sibling timestamp gets
    most_recent on the sibling, not on itself."""
    s = _signals(col_type={"address": "address"}, avg_len={"address": 25.0})
    result = _pick_strategy_for_field(
        "address", s,
        sibling_timestamp="updated_at",
        cardinality_ratio=0.5,
    )
    assert result is not None
    strategy, kwargs = result
    assert strategy == "most_recent"
    assert kwargs == {"date_column": "updated_at"}


def test_sibling_timestamp_does_not_apply_to_non_mutable_fields():
    """Field like `first_name` is not mutable -> sibling-timestamp rule
    does NOT fire even when one is present."""
    s = _signals(col_type={"first_name": "name"})
    result = _pick_strategy_for_field(
        "first_name", s,
        sibling_timestamp="updated_at",
    )
    # first_name isn't in _MUTABLE_NAME_PATTERNS; falls through to
    # other rules (which don't fire either on these signals).
    assert result is None


def test_compliance_pre_rule_overrides_post_cluster_signals():
    """Even if a compliance field has 0.7 null_rate (would match
    first_non_null) or 2.5 spread (would match confidence_majority),
    the pre-rule wins."""
    s = _signals(
        null_rate={"ssn": 0.7},
        within_cluster_spread={"ssn": 2.5},
    )
    result = _pick_strategy_for_field("ssn", s)
    assert result is not None
    strategy, _ = result
    assert strategy == "unanimous_or_null"


def test_pick_sibling_timestamp_picks_most_specific_match():
    """`updated_at` should beat `created_at` because updated_at appears
    earlier in the _TIMESTAMP_NAME_PATTERNS list."""
    from goldenmatch.core.autoconfig import ColumnProfile
    from goldenmatch.core.golden_rules_refiner import _pick_sibling_timestamp

    profiles = [
        ColumnProfile(
            name="updated_at", dtype="Datetime", col_type="date",
            confidence=0.9, null_rate=0.0, cardinality_ratio=0.9, avg_len=0,
        ),
        ColumnProfile(
            name="created_at", dtype="Datetime", col_type="date",
            confidence=0.9, null_rate=0.0, cardinality_ratio=0.9, avg_len=0,
        ),
    ]
    coverage = {"updated_at": 1.0, "created_at": 1.0}
    pick = _pick_sibling_timestamp(profiles, coverage)
    assert pick == "updated_at"


def test_pick_sibling_timestamp_returns_none_when_coverage_too_low():
    """Coverage < 80% -> no timestamp picked."""
    from goldenmatch.core.autoconfig import ColumnProfile
    from goldenmatch.core.golden_rules_refiner import _pick_sibling_timestamp

    profiles = [
        ColumnProfile(
            name="updated_at", dtype="Datetime", col_type="date",
            confidence=0.9, null_rate=0.0, cardinality_ratio=0.9, avg_len=0,
        ),
    ]
    coverage = {"updated_at": 0.5}  # below threshold
    pick = _pick_sibling_timestamp(profiles, coverage)
    assert pick is None


def test_picks_none_when_no_rule_applies():
    """Field with no remarkable signals -> defer to base default."""
    s = _signals()  # all defaults: low spread, low null, short string
    result = _pick_strategy_for_field("f", s)
    assert result is None


# ---------------------------------------------------------------------------
# refine_golden_rules integration
# ---------------------------------------------------------------------------


def test_refine_returns_base_rules_when_adaptive_false():
    """adaptive=False -> base_rules returned unchanged."""
    base = GoldenRulesConfig(default_strategy="most_complete", adaptive=False)
    clusters: dict[int, dict] = {}
    df = pl.DataFrame({"f": [1, 2]})
    out = refine_golden_rules(base, clusters, df, column_profiles=[])
    assert out is base  # same instance, not a copy


def test_refine_does_not_mutate_base_rules():
    """refine returns a NEW config; base.field_rules stays empty."""
    base = GoldenRulesConfig(default_strategy="most_complete", adaptive=True)
    clusters: dict[int, dict] = {}
    df = pl.DataFrame({"f": [1, 2]})
    refine_golden_rules(base, clusters, df, column_profiles=[])
    # Even with no clusters, the refiner should not mutate base.
    assert base.field_rules == {}


def test_refine_runs_without_clusters_returns_base():
    """No multi-member clusters -> nothing to refine; equal to base."""
    base = GoldenRulesConfig(default_strategy="most_complete", adaptive=True)
    clusters: dict[int, dict] = {}  # empty
    df = pl.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
    out = refine_golden_rules(base, clusters, df, column_profiles=[])
    assert out.field_rules == {}
    assert out.default_strategy == "most_complete"


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
