
# Ensure transforms are imported so they register
import goldenflow.transforms.categorical  # noqa: F401
import goldenflow.transforms.dates  # noqa: F401
import goldenflow.transforms.phone  # noqa: F401
import goldenflow.transforms.text  # noqa: F401
from goldenflow.engine.profiler_bridge import ColumnProfile
from goldenflow.engine.selector import (
    FINDING_TRANSFORM_MAP,
    select_from_findings,
    select_transforms,
)
from goldenflow.transforms import get_transform


def test_select_transforms_for_email_column():
    profile = ColumnProfile(
        name="email", inferred_type="email", row_count=100,
        null_count=0, null_pct=0.0, unique_count=100, unique_pct=1.0,
    )
    selected = select_transforms(profile)
    names = [t.name for t in selected]
    assert "strip" in names
    assert "lowercase" not in names  # not auto_apply for string, but email-specific logic


def test_select_transforms_for_phone_column():
    profile = ColumnProfile(
        name="phone", inferred_type="phone", row_count=100,
        null_count=0, null_pct=0.0, unique_count=100, unique_pct=1.0,
    )
    selected = select_transforms(profile)
    names = [t.name for t in selected]
    assert "phone_e164" in names


def test_select_transforms_for_date_column():
    profile = ColumnProfile(
        name="signup_date", inferred_type="date", row_count=100,
        null_count=0, null_pct=0.0, unique_count=50, unique_pct=0.5,
    )
    selected = select_transforms(profile)
    names = [t.name for t in selected]
    assert "date_iso8601" in names


def test_select_no_transforms_for_unknown_type():
    profile = ColumnProfile(
        name="misc", inferred_type="unknown_xyz", row_count=100,
        null_count=0, null_pct=0.0, unique_count=100, unique_pct=1.0,
    )
    selected = select_transforms(profile)
    # Only universal transforms (string-type with auto_apply) should match
    assert all(t.auto_apply for t in selected)


def test_auto_correct_excluded_for_high_cardinality():
    """category_auto_correct should not fire on high-cardinality columns."""
    import goldenflow.transforms.auto_correct  # noqa: F401
    profile = ColumnProfile(
        name="notes", inferred_type="string", row_count=1000,
        null_count=0, null_pct=0.0, unique_count=800, unique_pct=0.8,
    )
    selected = select_transforms(profile)
    names = [t.name for t in selected]
    assert "category_auto_correct" not in names


def test_auto_correct_included_for_low_cardinality():
    """category_auto_correct should fire on low-cardinality categorical columns."""
    import goldenflow.transforms.auto_correct  # noqa: F401
    profile = ColumnProfile(
        name="status", inferred_type="string", row_count=1000,
        null_count=0, null_pct=0.0, unique_count=5, unique_pct=0.005,
    )
    selected = select_transforms(profile)
    names = [t.name for t in selected]
    assert "category_auto_correct" in names


# --- FINDING_TRANSFORM_MAP tests ---


def test_finding_map_uses_real_goldencheck_check_names():
    """Map keys must match actual GoldenCheck check names, not invented labels."""
    real_check_names = {
        "type_inference", "nullability", "uniqueness", "format_detection",
        "range_distribution", "cardinality", "pattern_consistency",
        "encoding_detection", "sequence_detection", "drift_detection",
        "temporal_order", "null_correlation", "cross_column_validation",
        "cross_column",
    }
    # Every key in the map must be a real GoldenCheck check name
    for key in FINDING_TRANSFORM_MAP:
        assert key in real_check_names, f"'{key}' is not a real GoldenCheck check name"


def test_finding_map_transform_names_are_registered():
    """All transform names in the map must be registered in the transform registry."""
    import goldenflow  # noqa: F401 — ensure all transforms are registered
    for check, transform_names in FINDING_TRANSFORM_MAP.items():
        for name in transform_names:
            assert get_transform(name) is not None, (
                f"Transform '{name}' in FINDING_TRANSFORM_MAP['{check}'] is not registered"
            )


def test_finding_map_covers_actionable_checks():
    """All fixable GoldenCheck checks should have at least one transform mapping."""
    actionable_checks = [
        "format_detection", "nullability", "encoding_detection",
        "cardinality", "pattern_consistency", "type_inference",
        "temporal_order", "cross_column",
    ]
    for check in actionable_checks:
        assert check in FINDING_TRANSFORM_MAP, f"Missing mapping for '{check}'"
        assert len(FINDING_TRANSFORM_MAP[check]) > 0, f"Empty mapping for '{check}'"


def test_select_from_findings_format_detection():
    """format_detection findings should map to format-specific transforms."""
    findings = [
        {"check": "format_detection", "column": "email"},
        {"check": "format_detection", "column": "phone"},
    ]
    result = select_from_findings(findings)
    assert "email" in result
    assert "phone" in result
    # Should include relevant normalizers
    email_transforms = result["email"]
    phone_transforms = result["phone"]
    assert any("email" in t for t in email_transforms)
    assert any("phone" in t for t in phone_transforms)


def test_select_from_findings_nullability():
    """nullability findings should map to null_standardize."""
    findings = [{"check": "nullability", "column": "status"}]
    result = select_from_findings(findings)
    assert "status" in result
    assert "null_standardize" in result["status"]


def test_select_from_findings_encoding_detection():
    """encoding_detection findings should map to unicode/encoding fixes."""
    findings = [{"check": "encoding_detection", "column": "description"}]
    result = select_from_findings(findings)
    assert "description" in result
    assert "normalize_unicode" in result["description"]


def test_select_from_findings_cardinality():
    """cardinality findings should map to categorical correction."""
    findings = [{"check": "cardinality", "column": "status"}]
    result = select_from_findings(findings)
    assert "status" in result
    assert "category_auto_correct" in result["status"]


def test_select_from_findings_pattern_consistency():
    """pattern_consistency findings should map to format standardization."""
    findings = [{"check": "pattern_consistency", "column": "zip_code"}]
    result = select_from_findings(findings)
    assert "zip_code" in result
    assert "zip_normalize" in result["zip_code"]


def test_select_from_findings_multiple_findings_same_column():
    """Multiple findings on same column should combine and deduplicate transforms."""
    findings = [
        {"check": "encoding_detection", "column": "notes"},
        {"check": "nullability", "column": "notes"},
    ]
    result = select_from_findings(findings)
    assert "notes" in result
    assert "normalize_unicode" in result["notes"]
    assert "null_standardize" in result["notes"]


def test_select_from_findings_detection_only_checks_return_empty():
    """Detection-only checks (drift, null_correlation) should have no transforms."""
    findings = [
        {"check": "drift_detection", "column": "score"},
        {"check": "null_correlation", "column": "a,b"},
    ]
    result = select_from_findings(findings)
    # Detection-only checks map to empty lists, so no columns should have transforms
    assert result == {} or all(len(v) == 0 for v in result.values())


def test_select_from_findings_ignores_unknown_checks():
    """Unknown check names should be silently ignored."""
    findings = [{"check": "totally_fake_check", "column": "foo"}]
    result = select_from_findings(findings)
    assert "foo" not in result or len(result["foo"]) == 0
