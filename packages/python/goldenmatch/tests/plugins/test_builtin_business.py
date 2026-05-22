"""Tests for predefined business-shaped plugins (#predefined-merge-plugins).

Spec: docs/superpowers/specs/2026-05-22-predefined-merge-plugins-design.md
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from goldenmatch.plugins.builtin.business import (
    FreshnessWithMaxAgeStrategy,
    LifecycleStageStrategy,
    SystemOfRecordStrategy,
)

# ---------------------------------------------------------------------------
# system_of_record
# ---------------------------------------------------------------------------


def test_sor_picks_top_priority_source():
    val, conf, idx = SystemOfRecordStrategy().merge(
        values=["hub-val", "sfdc-val", "ns-val"],
        sources=["hubspot", "salesforce", "netsuite"],
        rule_kwargs={"source_priority": ["salesforce", "hubspot", "netsuite"]},
    )
    assert val == "sfdc-val"
    assert idx == 1
    assert conf == 1.0  # rank 0


def test_sor_falls_back_when_top_priority_null():
    val, conf, idx = SystemOfRecordStrategy().merge(
        values=[None, "hub-val", "ns-val"],
        sources=["salesforce", "hubspot", "netsuite"],
        rule_kwargs={"source_priority": ["salesforce", "hubspot", "netsuite"]},
    )
    assert val == "hub-val"
    assert idx == 1
    assert conf == 0.9  # rank 1 -> 1.0 - 0.1


def test_sor_no_priority_falls_back_to_first_non_null():
    val, conf, _idx = SystemOfRecordStrategy().merge(
        values=[None, "anything", "else"],
        sources=["foo", "bar", "baz"],
    )
    assert val == "anything"
    assert conf == 0.5  # fallback confidence


def test_sor_all_null():
    val, conf = SystemOfRecordStrategy().merge(
        values=[None, None],
        sources=["a", "b"],
        rule_kwargs={"source_priority": ["a"]},
    )
    assert val is None
    assert conf == 0.0


# ---------------------------------------------------------------------------
# lifecycle_stage
# ---------------------------------------------------------------------------


def test_lifecycle_picks_most_advanced():
    val, conf, idx = LifecycleStageStrategy().merge(["lead", "customer", "mql"])
    assert val == "customer"
    assert conf == 1.0
    assert idx == 1


def test_lifecycle_custom_order():
    val, _conf, _idx = LifecycleStageStrategy().merge(
        ["bronze", "gold", "silver"],
        rule_kwargs={"lifecycle_order": ["bronze", "silver", "gold", "platinum"]},
    )
    assert val == "gold"


def test_lifecycle_case_insensitive():
    val, _, _ = LifecycleStageStrategy().merge(["LEAD", "Customer", "MQL"])
    assert val == "Customer"


def test_lifecycle_unknown_value_ignored():
    """Unknown 'space_alien' ranks below all known stages."""
    val, _, _ = LifecycleStageStrategy().merge(["space_alien", "lead"])
    assert val == "lead"


def test_lifecycle_all_null():
    val, conf = LifecycleStageStrategy().merge([None, None])
    assert val is None
    assert conf == 0.0


def test_lifecycle_tied_stages():
    val, conf, idx = LifecycleStageStrategy().merge(["customer", "customer"])
    assert val == "customer"
    assert conf == 0.7  # tied
    assert idx == 0


# ---------------------------------------------------------------------------
# freshness_with_max_age
# ---------------------------------------------------------------------------


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def test_freshness_picks_newest_within_window():
    now = datetime.now(tz=UTC)
    fresh = _iso(now - timedelta(days=30))
    fresher = _iso(now - timedelta(days=10))
    val, conf, idx = FreshnessWithMaxAgeStrategy().merge(
        ["stale-val", "fresh-val"],
        dates=[fresh, fresher],
        rule_kwargs={"max_age_days": 90},
    )
    assert val == "fresh-val"
    assert conf == 1.0
    assert idx == 1


def test_freshness_emits_null_when_all_stale():
    now = datetime.now(tz=UTC)
    too_old = _iso(now - timedelta(days=400))
    val, conf = FreshnessWithMaxAgeStrategy().merge(
        ["stale-1", "stale-2"],
        dates=[too_old, too_old],
        rule_kwargs={"max_age_days": 90},
    )
    assert val is None
    assert conf == 0.0


def test_freshness_default_max_age_365():
    now = datetime.now(tz=UTC)
    fresh = _iso(now - timedelta(days=200))
    val, _, _ = FreshnessWithMaxAgeStrategy().merge(
        ["v1"], dates=[fresh],
    )
    assert val == "v1"


def test_freshness_no_dates_emits_null():
    val, conf = FreshnessWithMaxAgeStrategy().merge(["v1"], dates=None)
    assert val is None
    assert conf == 0.0


def test_freshness_handles_iso_with_z():
    now = datetime.now(tz=UTC)
    z_format = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    val, _, _ = FreshnessWithMaxAgeStrategy().merge(
        ["v1"], dates=[z_format], rule_kwargs={"max_age_days": 30},
    )
    assert val == "v1"


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
