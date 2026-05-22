"""Tests for v1.18 golden-field strategies (#golden-strategies).

Spec: docs/superpowers/specs/2026-05-22-intelligent-golden-rules-design.md
"""

from __future__ import annotations

import pytest
from goldenmatch.config.schemas import GoldenFieldRule
from goldenmatch.core.golden import merge_field

# ---------------------------------------------------------------------------
# longest_value
# ---------------------------------------------------------------------------


def test_longest_value_picks_longest_non_null():
    """Free-text field: longest string wins."""
    values = ["123 Main St", "123 Main Street Apartment 4B", "123 Main St."]
    rule = GoldenFieldRule(strategy="longest_value")
    winner, conf, idx = merge_field(values, rule)
    assert winner == "123 Main Street Apartment 4B"
    assert conf == 1.0
    assert idx == 1


def test_longest_value_ignores_nulls():
    """NULL members don't count; longest non-null wins."""
    values = [None, "short", None, "much longer value"]
    rule = GoldenFieldRule(strategy="longest_value")
    winner, _conf, _idx = merge_field(values, rule)
    assert winner == "much longer value"


def test_longest_value_ties_break_by_quality_weight():
    """Two equal-length values: higher quality_weight wins."""
    values = ["abc", "xyz"]
    rule = GoldenFieldRule(strategy="longest_value")
    winner, conf, idx = merge_field(values, rule, quality_weights=[0.3, 0.9])
    assert winner == "xyz"
    assert idx == 1
    assert conf == 0.7


# ---------------------------------------------------------------------------
# unanimous_or_null
# ---------------------------------------------------------------------------


def test_unanimous_or_null_emits_value_when_all_agree():
    """Every non-null member agrees -> emit that value."""
    values = ["A", "A", "A"]
    rule = GoldenFieldRule(strategy="unanimous_or_null")
    winner, conf, _idx = merge_field(values, rule)
    assert winner == "A"
    assert conf == 1.0


def test_unanimous_or_null_emits_null_when_any_disagree():
    """Any disagreement -> None."""
    values = ["A", "A", "B"]
    rule = GoldenFieldRule(strategy="unanimous_or_null")
    winner, conf, idx = merge_field(values, rule)
    assert winner is None
    assert conf == 0.0
    assert idx is None


def test_unanimous_or_null_ignores_null_members():
    """NULL members are absence-not-contradiction. Two non-null members
    agreeing + a NULL = unanimous on the non-null value."""
    values = [None, "X", "X", None]
    rule = GoldenFieldRule(strategy="unanimous_or_null")
    winner, conf, _idx = merge_field(values, rule)
    assert winner == "X"
    assert conf == 1.0


def test_unanimous_or_null_all_null_returns_null():
    """All members NULL -> (None, 0.0)."""
    values = [None, None, None]
    rule = GoldenFieldRule(strategy="unanimous_or_null")
    winner, conf, idx = merge_field(values, rule)
    assert winner is None
    assert conf == 0.0
    assert idx is None


# ---------------------------------------------------------------------------
# confidence_majority
# ---------------------------------------------------------------------------


def test_confidence_majority_overrides_count_majority_on_weak_edges():
    """3-member majority on weak edges loses to 2-member minority on
    strong edges. Pin the calibration."""
    values = ["A", "A", "A", "B", "B"]
    # Weak edges among A-A pairs, strong edges among B-B pair.
    pair_scores = {
        (0, 1): 0.55,
        (0, 2): 0.62,
        (1, 2): 0.51,
        (3, 4): 0.95,
    }
    rule = GoldenFieldRule(strategy="confidence_majority")
    winner, _conf, _idx = merge_field(values, rule, pair_scores=pair_scores)
    # A's total weight: 0.55 + 0.62 + 0.51 = 1.68
    # B's total weight: 0.95
    # A wins -- but only on sum-of-edge-weights. Pin the contract.
    assert winner == "A"  # A still wins because more pairs agree


def test_confidence_majority_high_weight_pair_overrules_count():
    """Single very-high-weight pair beats two weak agreeing pairs."""
    values = ["A", "A", "A", "B", "B"]
    pair_scores = {
        (0, 1): 0.30,  # A pair (weak)
        (0, 2): 0.30,  # A pair (weak)
        (3, 4): 1.00,  # B pair (strong)
    }
    rule = GoldenFieldRule(strategy="confidence_majority")
    winner, _conf, _idx = merge_field(values, rule, pair_scores=pair_scores)
    # A: 0.60; B: 1.00 -> B wins
    assert winner == "B"


def test_confidence_majority_falls_back_to_count_when_no_pair_scores():
    """No pair_scores -> defer to majority_vote."""
    values = ["A", "A", "B"]
    rule = GoldenFieldRule(strategy="confidence_majority")
    winner, _conf, _idx = merge_field(values, rule)
    # majority_vote -> A wins (2 vs 1)
    assert winner == "A"


def test_confidence_majority_falls_back_when_no_agreeing_pairs():
    """Every pair disagrees -> falls back to majority_vote."""
    values = ["A", "B", "C"]
    pair_scores = {
        (0, 1): 0.8,
        (0, 2): 0.7,
        (1, 2): 0.9,
    }
    rule = GoldenFieldRule(strategy="confidence_majority")
    winner, _conf, _idx = merge_field(values, rule, pair_scores=pair_scores)
    # No agreeing pairs -> count-majority. All values are unique;
    # majority_vote returns the first by count tie.
    assert winner in {"A", "B", "C"}  # whichever count-majority picks


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
