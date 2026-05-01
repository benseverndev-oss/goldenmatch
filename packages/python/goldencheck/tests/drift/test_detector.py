"""Tests for goldencheck.drift.detector — TDD drift detection."""
from __future__ import annotations


import numpy as np
import polars as pl

from goldencheck.baseline.models import (
    BaselineProfile,
    CorrelationEntry,
    FunctionalDependency,
    PatternGrammar,
    StatProfile,
    TemporalOrder,
)
from goldencheck.drift.detector import run_drift_checks
from goldencheck.models.finding import Severity


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_baseline(
    *,
    source: str = "test.csv",
    rows: int = 500,
    columns: list[str] | None = None,
    stat_profiles: dict | None = None,
    constraints_fd: list | None = None,
    constraints_keys: list | None = None,
    constraints_temporal: list | None = None,
    correlations: list | None = None,
    patterns: dict | None = None,
    semantic_types: dict | None = None,
) -> BaselineProfile:
    return BaselineProfile(
        source=source,
        rows=rows,
        columns=columns or [],
        stat_profiles=stat_profiles or {},
        constraints_fd=constraints_fd or [],
        constraints_keys=constraints_keys or [],
        constraints_temporal=constraints_temporal or [],
        correlations=correlations or [],
        patterns=patterns or {},
        semantic_types=semantic_types or {},
    )


def _rng(seed: int = 42) -> np.random.Generator:
    return np.random.default_rng(seed)


# ---------------------------------------------------------------------------
# All findings must have source="baseline_drift"
# ---------------------------------------------------------------------------


def test_all_findings_have_correct_source():
    """Every finding produced by run_drift_checks must have source='baseline_drift'."""
    rng = _rng()
    # Deliberately shifted distribution
    values = rng.normal(loc=100_000, scale=5_000, size=500).tolist()
    df = pl.DataFrame({"salary": values})

    sp = StatProfile(
        distribution="normal",
        params={"loc": 50_000.0, "scale": 5_000.0},
        entropy=5.0,
        bounds={"min": 30_000.0, "max": 70_000.0, "p01": 35_000.0, "p99": 65_000.0},
    )
    baseline = _make_baseline(stat_profiles={"salary": sp})
    findings = run_drift_checks(df, baseline)
    # There should be at least some findings, and all must have the right source
    assert len(findings) > 0
    for f in findings:
        assert f.source == "baseline_drift", f"source={f.source!r} for check={f.check!r}"


# ---------------------------------------------------------------------------
# Distribution drift (KS-test)
# ---------------------------------------------------------------------------


def test_detects_distribution_shift():
    """Should flag ERROR when distribution shifts from mu=50K to mu=100K."""
    rng = _rng()
    shifted = rng.normal(loc=100_000, scale=5_000, size=500).tolist()
    df = pl.DataFrame({"salary": shifted})

    sp = StatProfile(
        distribution="normal",
        params={"loc": 50_000.0, "scale": 5_000.0},
        entropy=5.0,
        bounds={"min": 30_000.0, "max": 70_000.0, "p01": 35_000.0, "p99": 65_000.0},
    )
    baseline = _make_baseline(stat_profiles={"salary": sp})
    findings = run_drift_checks(df, baseline)

    dist_findings = [f for f in findings if f.check == "distribution_drift"]
    assert len(dist_findings) >= 1
    f = dist_findings[0]
    assert f.severity == Severity.ERROR
    assert f.column == "salary"
    assert f.source == "baseline_drift"
    assert f.metadata["drift_type"] == "distribution_drift"
    assert f.metadata["technique"] == "statistical"


def test_no_distribution_drift_when_same():
    """Should NOT flag distribution drift when data matches baseline distribution."""
    rng = _rng()
    values = rng.normal(loc=50_000, scale=5_000, size=500).tolist()
    df = pl.DataFrame({"salary": values})

    sp = StatProfile(
        distribution="normal",
        params={"loc": 50_000.0, "scale": 5_000.0},
        entropy=5.0,
        bounds={"min": 30_000.0, "max": 70_000.0, "p01": 35_000.0, "p99": 65_000.0},
    )
    baseline = _make_baseline(stat_profiles={"salary": sp})
    findings = run_drift_checks(df, baseline)

    dist_findings = [f for f in findings if f.check == "distribution_drift"]
    assert len(dist_findings) == 0


# ---------------------------------------------------------------------------
# Bound violation
# ---------------------------------------------------------------------------


def test_detects_bound_violation():
    """Should flag ERROR when > 5% of values are outside baseline p01/p99 bounds."""
    rng = _rng()
    # 400 normal values in range + 100 extreme outliers
    normal_part = rng.normal(loc=50.0, scale=5.0, size=400).tolist()
    extreme_part = rng.normal(loc=500.0, scale=5.0, size=100).tolist()
    values = normal_part + extreme_part
    df = pl.DataFrame({"score": values})

    sp = StatProfile(
        distribution="normal",
        params={"loc": 50.0, "scale": 5.0},
        entropy=5.0,
        bounds={"min": 30.0, "max": 70.0, "p01": 35.0, "p99": 65.0},
    )
    baseline = _make_baseline(stat_profiles={"score": sp})
    findings = run_drift_checks(df, baseline)

    bound_findings = [f for f in findings if f.check == "bound_violation"]
    assert len(bound_findings) >= 1
    f = bound_findings[0]
    assert f.severity == Severity.ERROR
    assert f.column == "score"
    assert f.source == "baseline_drift"
    assert f.metadata["drift_type"] == "bound_violation"
    assert f.metadata["violation_rate"] > 0.05


def test_no_bound_violation_within_bounds():
    """Should NOT flag bound violation when data stays within baseline bounds."""
    rng = _rng()
    values = rng.normal(loc=50.0, scale=3.0, size=500).tolist()
    df = pl.DataFrame({"score": values})

    sp = StatProfile(
        distribution="normal",
        params={"loc": 50.0, "scale": 3.0},
        entropy=5.0,
        bounds={"min": 30.0, "max": 70.0, "p01": 40.0, "p99": 60.0},
    )
    baseline = _make_baseline(stat_profiles={"score": sp})
    findings = run_drift_checks(df, baseline)

    bound_findings = [f for f in findings if f.check == "bound_violation"]
    assert len(bound_findings) == 0


# ---------------------------------------------------------------------------
# Entropy drift
# ---------------------------------------------------------------------------


def test_detects_entropy_drift():
    """Should flag WARNING when entropy shifts significantly."""
    # Baseline: wide spread → high entropy. Current: all clustered at 1 value → near 0
    values = [1.0] * 450 + [2.0] * 50
    df = pl.DataFrame({"status_code": values})

    sp = StatProfile(
        distribution=None,
        params=None,
        entropy=8.0,  # very high baseline entropy
        bounds={"min": 1.0, "max": 100.0, "p01": 1.0, "p99": 99.0},
    )
    baseline = _make_baseline(stat_profiles={"status_code": sp})
    findings = run_drift_checks(df, baseline)

    entropy_findings = [f for f in findings if f.check == "entropy_drift"]
    assert len(entropy_findings) >= 1
    f = entropy_findings[0]
    assert f.severity == Severity.WARNING
    assert f.column == "status_code"
    assert f.source == "baseline_drift"
    assert f.metadata["drift_type"] == "entropy_drift"
    assert f.metadata["delta"] > 0.5


# ---------------------------------------------------------------------------
# FD violation
# ---------------------------------------------------------------------------


def test_detects_fd_violation():
    """Should flag ERROR when zip→city functional dependency breaks."""
    # Baseline: zip always maps to same city (100% confidence)
    fd = FunctionalDependency(
        determinant=["zip"],
        dependent=["city"],
        confidence=1.0,
    )
    baseline = _make_baseline(constraints_fd=[fd])

    # Current: zip maps to multiple cities (violation)
    df = pl.DataFrame({
        "zip": ["10001", "10001", "10001", "10001", "10002"] * 60,
        "city": ["New York", "New York", "Brooklyn", "Queens", "Jersey City"] * 60,
    })
    findings = run_drift_checks(df, baseline)

    fd_findings = [f for f in findings if f.check == "fd_violation"]
    assert len(fd_findings) >= 1
    f = fd_findings[0]
    assert f.severity == Severity.ERROR
    assert f.column == "zip"
    assert f.source == "baseline_drift"
    assert f.metadata["drift_type"] == "fd_violation"
    assert f.metadata["current_violation_rate"] > 0.0


def test_no_fd_violation_when_consistent():
    """Should NOT flag FD violation when relationship is still consistent."""
    fd = FunctionalDependency(
        determinant=["zip"],
        dependent=["city"],
        confidence=1.0,
    )
    baseline = _make_baseline(constraints_fd=[fd])

    df = pl.DataFrame({
        "zip": ["10001", "10001", "10002", "10002"] * 100,
        "city": ["New York", "New York", "Jersey City", "Jersey City"] * 100,
    })
    findings = run_drift_checks(df, baseline)

    fd_findings = [f for f in findings if f.check == "fd_violation"]
    assert len(fd_findings) == 0


# ---------------------------------------------------------------------------
# Key uniqueness loss
# ---------------------------------------------------------------------------


def test_detects_key_uniqueness_loss():
    """Should flag ERROR when a candidate key gains duplicates."""
    baseline = _make_baseline(constraints_keys=[["order_id"]])

    df = pl.DataFrame({
        "order_id": [1, 2, 2, 3, 4, 5, 5, 6, 7, 8] * 5,  # duplicates
        "amount": [10.0, 20.0, 20.0, 30.0, 40.0, 50.0, 50.0, 60.0, 70.0, 80.0] * 5,
    })
    findings = run_drift_checks(df, baseline)

    key_findings = [f for f in findings if f.check == "key_uniqueness_loss"]
    assert len(key_findings) >= 1
    f = key_findings[0]
    assert f.severity == Severity.ERROR
    assert f.column == "order_id"
    assert f.source == "baseline_drift"
    assert f.metadata["drift_type"] == "key_uniqueness_loss"
    assert f.metadata["duplicate_count"] > 0


def test_no_key_uniqueness_loss_when_unique():
    """Should NOT flag key uniqueness loss when column is still unique."""
    baseline = _make_baseline(constraints_keys=[["order_id"]])

    df = pl.DataFrame({
        "order_id": list(range(1, 101)),
        "amount": [float(i) for i in range(1, 101)],
    })
    findings = run_drift_checks(df, baseline)

    key_findings = [f for f in findings if f.check == "key_uniqueness_loss"]
    assert len(key_findings) == 0


# ---------------------------------------------------------------------------
# Temporal order drift
# ---------------------------------------------------------------------------


def test_detects_temporal_order_drift():
    """Should flag WARNING when temporal violation rate increases significantly."""
    to = TemporalOrder(before="order_date", after="ship_date", violation_rate=0.01)
    baseline = _make_baseline(constraints_temporal=[to])

    # Create data where ship_date is frequently before order_date
    import datetime as dt
    base_date = dt.date(2024, 1, 1)
    n = 200
    order_dates = []
    ship_dates = []
    for i in range(n):
        order_d = base_date + dt.timedelta(days=i % 30)
        if i < 120:  # 60% violations: ship before order
            ship_d = order_d - dt.timedelta(days=5)
        else:
            ship_d = order_d + dt.timedelta(days=3)
        order_dates.append(order_d)
        ship_dates.append(ship_d)

    df = pl.DataFrame({
        "order_date": pl.Series(order_dates),
        "ship_date": pl.Series(ship_dates),
    })
    findings = run_drift_checks(df, baseline)

    temporal_findings = [f for f in findings if f.check == "temporal_order_drift"]
    assert len(temporal_findings) >= 1
    f = temporal_findings[0]
    assert f.severity == Severity.WARNING
    assert f.column == "order_date"
    assert f.source == "baseline_drift"
    assert f.metadata["drift_type"] == "temporal_order_drift"
    assert f.metadata["current_violation_rate"] > 0.05


def test_no_temporal_drift_when_consistent():
    """Should NOT flag temporal drift when ordering is still consistent."""
    to = TemporalOrder(before="order_date", after="ship_date", violation_rate=0.0)
    baseline = _make_baseline(constraints_temporal=[to])

    import datetime as dt
    base_date = dt.date(2024, 1, 1)
    n = 100
    order_dates = [base_date + dt.timedelta(days=i) for i in range(n)]
    ship_dates = [d + dt.timedelta(days=3) for d in order_dates]

    df = pl.DataFrame({
        "order_date": pl.Series(order_dates),
        "ship_date": pl.Series(ship_dates),
    })
    findings = run_drift_checks(df, baseline)

    temporal_findings = [f for f in findings if f.check == "temporal_order_drift"]
    assert len(temporal_findings) == 0


# ---------------------------------------------------------------------------
# Pattern drift
# ---------------------------------------------------------------------------


def test_detects_pattern_coverage_drop():
    """Should flag WARNING when baseline pattern coverage drops > 5%."""
    pg = PatternGrammar(pattern="[A-Z]{2}-[0-9]{4}", coverage=0.95)
    baseline = _make_baseline(patterns={"product_code": pg})

    # Current: mostly different format
    values = (
        ["AB-1234"] * 30  # matches baseline pattern
        + ["XX9999"] * 200  # new pattern — no hyphen
        + ["ZZ0000"] * 100
    )
    df = pl.DataFrame({"product_code": values})
    findings = run_drift_checks(df, baseline)

    pattern_findings = [f for f in findings if f.check == "pattern_drift"]
    assert len(pattern_findings) >= 1
    f = pattern_findings[0]
    assert f.severity == Severity.WARNING
    assert f.column == "product_code"
    assert f.source == "baseline_drift"
    assert f.metadata["drift_type"] == "pattern_drift"
    assert f.metadata["drop"] > 0.05


def test_detects_new_pattern():
    """Should flag INFO for new format variants with > 5% coverage."""
    pg = PatternGrammar(pattern="[A-Z]{2}-[0-9]{4}", coverage=0.95)
    baseline = _make_baseline(patterns={"product_code": pg})

    # Current: mostly same format, but a new pattern appears with 20% coverage
    values = (
        ["AB-1234"] * 240  # matches baseline (~72%)
        + ["XY/5678"] * 90  # new pattern (~27%)
    )
    df = pl.DataFrame({"product_code": values})
    findings = run_drift_checks(df, baseline)

    new_findings = [f for f in findings if f.check == "new_pattern"]
    assert len(new_findings) >= 1
    f = new_findings[0]
    assert f.severity == Severity.INFO
    assert f.column == "product_code"
    assert f.source == "baseline_drift"
    assert f.metadata["drift_type"] == "new_pattern"
    assert f.metadata["coverage"] > 0.05


# ---------------------------------------------------------------------------
# Correlation checks
# ---------------------------------------------------------------------------


def test_detects_correlation_break():
    """Should flag WARNING when a strong Pearson correlation drops > 0.1."""
    entry = CorrelationEntry(
        columns=["x", "y"],
        measure="pearson",
        value=0.95,
        strength="strong",
    )
    baseline = _make_baseline(correlations=[entry])

    rng = _rng()
    # Uncorrelated data
    x = rng.normal(0, 1, 300).tolist()
    y = rng.normal(0, 1, 300).tolist()
    df = pl.DataFrame({"x": x, "y": y})

    findings = run_drift_checks(df, baseline)

    corr_findings = [f for f in findings if f.check == "correlation_break"]
    assert len(corr_findings) >= 1
    f = corr_findings[0]
    assert f.severity == Severity.WARNING
    assert f.source == "baseline_drift"
    assert f.metadata["drift_type"] == "correlation_break"


# ---------------------------------------------------------------------------
# Semantic checks
# ---------------------------------------------------------------------------


def test_no_semantic_drift_when_types_match():
    """Should NOT flag type drift when semantic types are unchanged."""
    baseline = _make_baseline(semantic_types={"email_address": "email"})
    df = pl.DataFrame({"email_address": ["a@b.com"] * 50})
    findings = run_drift_checks(df, baseline)
    type_findings = [f for f in findings if f.check == "type_drift"]
    # email_address should still be inferred as email, no drift
    assert len(type_findings) == 0


# ---------------------------------------------------------------------------
# Metadata completeness
# ---------------------------------------------------------------------------


def test_metadata_has_required_keys():
    """All findings must have 'technique' and 'drift_type' in metadata."""
    rng = _rng()
    shifted = rng.normal(loc=999_999, scale=1_000, size=500).tolist()
    df = pl.DataFrame({"price": shifted})

    sp = StatProfile(
        distribution="normal",
        params={"loc": 50.0, "scale": 5.0},
        entropy=4.0,
        bounds={"min": 30.0, "max": 70.0, "p01": 35.0, "p99": 65.0},
    )
    baseline = _make_baseline(stat_profiles={"price": sp})
    findings = run_drift_checks(df, baseline)

    assert len(findings) > 0
    for f in findings:
        assert "technique" in f.metadata, f"Missing 'technique' in {f.check!r} metadata"
        assert "drift_type" in f.metadata, f"Missing 'drift_type' in {f.check!r} metadata"


# ---------------------------------------------------------------------------
# Benford drift
# ---------------------------------------------------------------------------


def test_detects_benford_drift():
    """Should flag WARNING when Benford conformance flips from passing to failing."""
    rng = _rng()
    # Baseline: conforms to Benford (high p-value means no significant deviation)
    sp = StatProfile(
        distribution=None,
        params=None,
        benford={"chi2_pvalue": 0.85},  # baseline conformed (p >= 0.05)
        entropy=5.0,
        bounds={"min": 1.0, "max": 1_000_000.0, "p01": 1.0, "p99": 999_999.0},
    )
    baseline = _make_baseline(stat_profiles={"total_amount": sp})

    # Current: values that span 2+ orders of magnitude but all start with digit 5
    # This violates Benford's law (which expects ~30% starting with 1)
    values = (
        rng.uniform(5, 9.99, size=100).tolist()
        + rng.uniform(50, 99.9, size=100).tolist()
        + rng.uniform(500, 999, size=100).tolist()
        + rng.uniform(5000, 9999, size=100).tolist()
        + rng.uniform(50000, 99999, size=100).tolist()
    )
    df = pl.DataFrame({"total_amount": values})

    findings = run_drift_checks(df, baseline)
    benford_findings = [f for f in findings if f.check == "benford_drift"]
    assert len(benford_findings) >= 1
    f = benford_findings[0]
    assert f.severity == Severity.WARNING
    assert f.column == "total_amount"
    assert f.source == "baseline_drift"
    assert f.metadata["drift_type"] == "benford_drift"


# ---------------------------------------------------------------------------
# New correlation
# ---------------------------------------------------------------------------


def test_detects_new_correlation():
    """Should flag INFO when a new strong correlation appears not in baseline."""
    # Baseline has no correlations
    baseline = _make_baseline(correlations=[], columns=["x", "y"])

    # Current: perfectly correlated
    rng = _rng()
    x = rng.normal(0, 1, 300).tolist()
    y = [v * 2.0 + 1.0 for v in x]  # y = 2x + 1, perfect linear correlation
    df = pl.DataFrame({"x": x, "y": y})

    findings = run_drift_checks(df, baseline)
    new_corr_findings = [f for f in findings if f.check == "new_correlation"]
    assert len(new_corr_findings) >= 1
    f = new_corr_findings[0]
    assert f.severity == Severity.INFO
    assert f.source == "baseline_drift"
    assert f.metadata["drift_type"] == "new_correlation"


# ---------------------------------------------------------------------------
# Type drift (positive)
# ---------------------------------------------------------------------------


def test_detects_type_drift():
    """Should flag WARNING when a column's semantic type changes."""
    # Baseline says 'contact' is an email column
    baseline = _make_baseline(semantic_types={"contact": "email"})

    # Current: 'contact' now contains phone numbers — keyword matcher should
    # classify it as 'phone' since the values look like phone numbers and the
    # column name 'contact' doesn't match email keywords.
    df = pl.DataFrame({
        "contact": ["555-1234", "555-5678", "555-9012", "555-3456", "555-7890"] * 20,
    })

    findings = run_drift_checks(df, baseline)
    _ = [f for f in findings if f.check == "type_drift"]
    # The keyword matcher classifies "contact" as neither email nor phone
    # (no email/phone keyword in "contact"), so current_type will be None
    # and no drift is flagged. Instead, use a column name with a phone keyword.

    # Re-test with a column name that triggers phone classification
    baseline2 = _make_baseline(semantic_types={"phone_contact": "email"})
    df2 = pl.DataFrame({
        "phone_contact": ["555-1234", "555-5678", "555-9012"] * 20,
    })
    findings2 = run_drift_checks(df2, baseline2)
    type_findings2 = [f for f in findings2 if f.check == "type_drift"]
    assert len(type_findings2) >= 1
    f = type_findings2[0]
    assert f.severity == Severity.WARNING
    assert f.column == "phone_contact"
    assert f.source == "baseline_drift"
    assert f.metadata["drift_type"] == "type_drift"
    assert f.metadata["baseline_type"] == "email"
    assert f.metadata["current_type"] == "phone"


# ---------------------------------------------------------------------------
# Empty / missing columns
# ---------------------------------------------------------------------------


def test_missing_column_skipped():
    """Baseline columns not present in current df should be silently skipped."""
    sp = StatProfile(
        distribution="normal",
        params={"loc": 50.0, "scale": 5.0},
        entropy=5.0,
        bounds={"min": 30.0, "max": 70.0, "p01": 35.0, "p99": 65.0},
    )
    baseline = _make_baseline(stat_profiles={"revenue": sp})

    # DataFrame doesn't have 'revenue'
    df = pl.DataFrame({"other_col": [1.0, 2.0, 3.0] * 20})
    findings = run_drift_checks(df, baseline)
    # Should not raise, may return 0 findings
    assert isinstance(findings, list)
