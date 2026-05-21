"""Tests for the postflight surface + real-world fixtures (#404).

Step 7: PostflightReport renders the exclusion list as a separate
section.

Step 8: real-world-shaped fixtures (NCVR-style with audit columns,
CRM-style with external_id + lifecycle + hash, clinical-style with
sentinel phone + free-text notes) prove the detectors land the
expected exclusions.
"""

from __future__ import annotations

import datetime

import polars as pl
import pytest
from goldenmatch.core.autoconfig_verify import PostflightReport
from goldenmatch.core.quality_exclusions import ExcludedColumn

# ---------------------------------------------------------------------------
# Step 7: PostflightReport rendering
# ---------------------------------------------------------------------------


def test_postflight_renders_exclusion_section_when_non_empty():
    """When autoconfig_exclusions is non-empty, __str__ includes an
    'auto-config exclusions:' section with one line per exclusion."""
    pf = PostflightReport()
    pf.autoconfig_exclusions = [
        ExcludedColumn(
            column="created_at",
            detector="audit_column",
            reason="audit timestamp column (high cardinality, no identity signal)",
            evidence={},
        ),
        ExcludedColumn(
            column="phone",
            detector="sentinel_values",
            reason="sentinel/placeholder values in 23% of records",
            evidence={},
        ),
    ]
    rendered = str(pf)
    assert "auto-config exclusions:" in rendered
    assert "'created_at': audit_column" in rendered
    assert "'phone': sentinel_values" in rendered
    assert "audit timestamp" in rendered
    assert "23% of records" in rendered


def test_postflight_omits_exclusion_section_when_empty():
    """Clean datasets shouldn't render an empty 'auto-config
    exclusions:' header."""
    pf = PostflightReport()
    assert pf.autoconfig_exclusions == []
    rendered = str(pf)
    assert "auto-config exclusions:" not in rendered


# ---------------------------------------------------------------------------
# Step 7 (E2E): exclusions flow from auto_configure_df -> dedupe_df ->
# DedupeResult.postflight_report
# ---------------------------------------------------------------------------


def test_dedupe_df_postflight_surfaces_exclusions_end_to_end():
    """The user-visible audit trail: run a poisoned df through
    dedupe_df, inspect result.postflight_report.autoconfig_exclusions."""
    import goldenmatch

    df = pl.DataFrame({
        "first_name": [f"name_{i}" for i in range(100)],
        "last_name": [f"smith_{i % 10}" for i in range(100)],
        "external_id": [f"ext_{i:08d}" for i in range(100)],  # foreign_system_id
        "created_at": [
            datetime.datetime(2026, 1, 1) + datetime.timedelta(seconds=i)
            for i in range(100)
        ],  # audit_column
    })

    result = goldenmatch.dedupe_df(df, confidence_required=False)
    pf = result.postflight_report
    assert pf is not None, "postflight report must be populated on zero-config path"
    assert pf.autoconfig_exclusions, "expected exclusions on poisoned df"
    excluded_cols = {ec.column for ec in pf.autoconfig_exclusions}
    assert "external_id" in excluded_cols
    assert "created_at" in excluded_cols


# ---------------------------------------------------------------------------
# Step 8: real-world-shaped fixtures
# ---------------------------------------------------------------------------


def _ncvr_style_fixture(n: int = 100) -> pl.DataFrame:
    """NC voter-registration-shaped data: name + dob + audit timestamps.
    The audit columns must be excluded; everything else stays."""
    return pl.DataFrame({
        "first_name": [f"FirstName_{i}" for i in range(n)],
        "last_name": [f"LastName_{i % 25}" for i in range(n)],
        "birth_year": [1950 + (i % 70) for i in range(n)],
        "zip_code": [f"{27000 + (i % 50):05d}" for i in range(n)],
        "created_at": [
            datetime.datetime(2024, 1, 1) + datetime.timedelta(seconds=i)
            for i in range(n)
        ],
        "updated_at": [
            datetime.datetime(2026, 1, 1) + datetime.timedelta(seconds=i)
            for i in range(n)
        ],
    })


def _crm_style_fixture(n: int = 100) -> pl.DataFrame:
    """CRM-shaped data: name + email + external_id + lifecycle + hash.
    external_id, is_active, record_hash should all be excluded."""
    return pl.DataFrame({
        "name": [f"customer_{i}" for i in range(n)],
        "email": [f"customer{i}@company.com" for i in range(n)],
        "external_id": [f"CRM-{i:010d}" for i in range(n)],
        "is_active": [i % 5 != 0 for i in range(n)],
        "record_hash": [f"{i:032x}" for i in range(n)],
    })


def _clinical_style_fixture(n: int = 100) -> pl.DataFrame:
    """Clinical-shaped data: patient name + dob + phone (40% sentinel)
    + free-text notes. Phone and notes should be excluded."""
    phones = ["555-1234"] * 60 + ["0000000000"] * 40
    return pl.DataFrame({
        "patient_name": [f"Patient_{i}" for i in range(n)],
        "dob": [
            datetime.date(1950, 1, 1) + datetime.timedelta(days=i * 100)
            for i in range(n)
        ],
        "phone": phones,
        "notes": [
            "a" * 150  # long free text
            for _ in range(n)
        ],
    })


def test_ncvr_style_excludes_audit_columns():
    """NCVR fixture: created_at + updated_at are audit columns; first/
    last_name + birth_year + zip_code stay."""
    from goldenmatch.core.quality_exclusions import detect_autoconfig_exclusions

    df = _ncvr_style_fixture()
    excluded = detect_autoconfig_exclusions(df)
    excluded_pairs = {(ec.column, ec.detector) for ec in excluded}

    assert ("created_at", "audit_column") in excluded_pairs
    assert ("updated_at", "audit_column") in excluded_pairs
    # Identity columns must NOT be excluded.
    excluded_cols = {ec.column for ec in excluded}
    for kept in ["first_name", "last_name", "birth_year", "zip_code"]:
        assert kept not in excluded_cols, (
            f"{kept!r} must NOT be excluded -- it's the actual identity signal"
        )


def test_crm_style_excludes_external_id_lifecycle_hash():
    """CRM fixture: external_id + is_active + record_hash must all
    be excluded; name + email stay."""
    from goldenmatch.core.quality_exclusions import detect_autoconfig_exclusions

    df = _crm_style_fixture()
    excluded = detect_autoconfig_exclusions(df)
    excluded_pairs = {(ec.column, ec.detector) for ec in excluded}

    assert ("external_id", "foreign_system_id") in excluded_pairs
    assert ("is_active", "soft_delete_flag") in excluded_pairs
    assert ("record_hash", "system_hash") in excluded_pairs

    excluded_cols = {ec.column for ec in excluded}
    for kept in ["name", "email"]:
        assert kept not in excluded_cols


def test_clinical_style_excludes_sentinel_phone_and_free_text_notes():
    """Clinical fixture: phone (40% sentinel) + notes (long free text)
    must be excluded; patient_name + dob stay."""
    from goldenmatch.core.quality_exclusions import detect_autoconfig_exclusions

    df = _clinical_style_fixture()
    excluded = detect_autoconfig_exclusions(df)
    excluded_pairs = {(ec.column, ec.detector) for ec in excluded}

    assert ("phone", "sentinel_values") in excluded_pairs
    assert ("notes", "free_text_notes") in excluded_pairs

    excluded_cols = {ec.column for ec in excluded}
    for kept in ["patient_name", "dob"]:
        assert kept not in excluded_cols


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
