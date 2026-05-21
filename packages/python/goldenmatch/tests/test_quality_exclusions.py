"""Tests for the GoldenCheck auto-config exclusion detectors (#404).

Spec: docs/superpowers/specs/2026-05-21-goldencheck-autoconfig-exclusions-design.md
Plan: docs/superpowers/plans/2026-05-21-goldencheck-autoconfig-exclusions.md
"""

from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.core.quality_exclusions import (
    ColumnProfile,
    ExcludedColumn,
    detect_audit_column,
    detect_autoconfig_exclusions,
    detect_foreign_system_id,
    detect_free_text_notes,
    detect_sentinel_values,
    detect_soft_delete_flag,
    detect_system_hash,
)


def _profile(
    *,
    cardinality_ratio: float = 0.5,
    null_rate: float = 0.0,
    distinct_count: int = 50,
    dtype: str = "Utf8",
    mean_string_length: float | None = None,
) -> ColumnProfile:
    return ColumnProfile(
        cardinality_ratio=cardinality_ratio,
        null_rate=null_rate,
        distinct_count=distinct_count,
        dtype=dtype,
        mean_string_length=mean_string_length,
    )


# ---------------------------------------------------------------------------
# Dataclass shape
# ---------------------------------------------------------------------------


def test_excluded_column_is_immutable():
    """ExcludedColumn is frozen so it can be hashed / cached without
    defensive copying."""
    ec = ExcludedColumn(
        column="x", detector="audit_column",
        reason="...", evidence={},
    )
    with pytest.raises((AttributeError, Exception)):
        ec.column = "y"  # type: ignore[misc]


def test_column_profile_is_immutable():
    """ColumnProfile is the cheap stats struct; frozen so detectors
    can't mutate it mid-run."""
    p = _profile()
    with pytest.raises((AttributeError, Exception)):
        p.cardinality_ratio = 0.99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Detector 1: audit_column
# ---------------------------------------------------------------------------


def test_audit_column_excludes_created_at_with_high_cardinality():
    profile = _profile(
        dtype="Datetime(time_unit='us', time_zone=None)",
        cardinality_ratio=0.91,
    )
    result = detect_audit_column("created_at", [], profile)
    assert result is not None
    assert result.detector == "audit_column"
    assert "audit timestamp" in result.reason


def test_audit_column_excludes_event_at_suffix_pattern():
    profile = _profile(
        dtype="Datetime(time_unit='us', time_zone=None)",
        cardinality_ratio=0.88,
    )
    result = detect_audit_column("event_at", [], profile)
    assert result is not None
    assert result.detector == "audit_column"


def test_audit_column_passes_random_string_field():
    profile = _profile(dtype="Utf8", cardinality_ratio=0.5)
    assert detect_audit_column("first_name", [], profile) is None


# ---------------------------------------------------------------------------
# Detector 2: foreign_system_id
# ---------------------------------------------------------------------------


def test_foreign_system_id_excludes_external_id_high_cardinality():
    profile = _profile(cardinality_ratio=0.99, distinct_count=990)
    result = detect_foreign_system_id("external_id", [], profile)
    assert result is not None
    assert result.detector == "foreign_system_id"
    assert "per-source" in result.reason


def test_foreign_system_id_excludes_record_uuid_suffix():
    profile = _profile(cardinality_ratio=0.99)
    result = detect_foreign_system_id("record_uuid", [], profile)
    assert result is not None


def test_foreign_system_id_passes_low_cardinality_id_field():
    # `customer_id` with cardinality 0.5 is NOT a foreign-system ID.
    # Detector requires name pattern AND high cardinality.
    profile = _profile(cardinality_ratio=0.5)
    assert detect_foreign_system_id("customer_id", [], profile) is None


# ---------------------------------------------------------------------------
# Detector 3: sentinel_values (THE #1 poison)
# ---------------------------------------------------------------------------


def test_sentinel_values_excludes_phone_column_with_30_pct_zeros():
    """Phone column with 30% sentinel values must be excluded."""
    sampled = ["555-1234"] * 70 + ["0000000000"] * 30
    profile = _profile(dtype="Utf8", mean_string_length=10)
    result = detect_sentinel_values("phone", sampled, profile)
    assert result is not None
    assert result.detector == "sentinel_values"
    assert "30%" in result.reason
    assert result.evidence["sentinel_rate"] == pytest.approx(0.30, abs=0.01)


def test_sentinel_values_excludes_email_with_noreply_substring():
    sampled = ["alice@example.org"] * 60 + ["noreply@example.com"] * 40
    profile = _profile(dtype="Utf8", mean_string_length=20)
    result = detect_sentinel_values("email", sampled, profile)
    assert result is not None
    # @example.com hits the substring sentinel for ALL example.org +
    # all noreply rows -- the detector must catch at least the 40%
    # noreply rate.
    assert result.evidence["sentinel_rate"] >= 0.40


def test_sentinel_values_excludes_string_unknown_sentinels():
    sampled = ["Alice", "Bob", "Carol"] * 30 + ["Unknown"] * 20 + ["N/A"] * 5
    profile = _profile(dtype="Utf8", mean_string_length=5)
    result = detect_sentinel_values("first_name", sampled, profile)
    assert result is not None
    # 25 / (90 + 25) = ~21.7%
    assert result.evidence["sentinel_rate"] >= 0.20


def test_sentinel_values_passes_clean_column_under_threshold():
    """5% sentinel rate is below the 10% threshold -- emit no
    exclusion (might still be a WARN-level Finding but not an
    auto-config exclusion)."""
    profile = _profile(dtype="Utf8", mean_string_length=20)
    # @example.com substring would match all "alice@example.org" rows
    # too -- pick a fixture that genuinely has only 5% sentinel rate.
    sampled_clean = [f"alice{i}@company.com" for i in range(95)] + ["test@test.com"] * 5
    result_clean = detect_sentinel_values("email", sampled_clean, profile)
    assert result_clean is None, (
        "5% sentinel rate (test@test.com substring) under 10% threshold "
        "must not produce an exclusion"
    )


def test_sentinel_values_passes_non_string_column():
    """Sentinels are a string-column concept. Integer / float columns
    are not the target."""
    profile = _profile(dtype="Int64", mean_string_length=None)
    assert detect_sentinel_values(
        "age", [25, 30, 35, 40], profile,
    ) is None


# ---------------------------------------------------------------------------
# Detector 4: soft_delete_flag
# ---------------------------------------------------------------------------


def test_soft_delete_flag_excludes_is_active_boolean():
    profile = _profile(
        cardinality_ratio=0.0001,
        distinct_count=2,
        dtype="Boolean",
    )
    result = detect_soft_delete_flag(
        "is_active", [True, False, True, True], profile,
    )
    assert result is not None
    assert result.detector == "soft_delete_flag"


def test_soft_delete_flag_excludes_status_low_cardinality():
    profile = _profile(
        cardinality_ratio=0.0005,
        distinct_count=4,
        dtype="Utf8",
        mean_string_length=8,
    )
    sampled = ["active"] * 50 + ["inactive"] * 30 + ["pending"] * 20 + ["deleted"] * 5
    result = detect_soft_delete_flag("status", sampled, profile)
    assert result is not None


def test_soft_delete_flag_passes_high_cardinality_status():
    """A 'status' column with thousands of distinct values isn't a
    lifecycle flag -- it's something custom."""
    profile = _profile(cardinality_ratio=0.5, distinct_count=5000)
    assert detect_soft_delete_flag("status", [], profile) is None


# ---------------------------------------------------------------------------
# Detector 5: free_text_notes
# ---------------------------------------------------------------------------


def test_free_text_notes_excludes_long_description():
    profile = _profile(dtype="Utf8", mean_string_length=187)
    result = detect_free_text_notes("description", [], profile)
    assert result is not None
    assert result.detector == "free_text_notes"


def test_free_text_notes_excludes_customer_comments_suffix():
    profile = _profile(dtype="Utf8", mean_string_length=80)
    result = detect_free_text_notes("customer_comments", [], profile)
    assert result is not None


def test_free_text_notes_passes_short_label_field():
    """A 'notes' column with mean length 8 is just a label, not free
    text."""
    profile = _profile(dtype="Utf8", mean_string_length=8)
    assert detect_free_text_notes("notes", [], profile) is None


# ---------------------------------------------------------------------------
# Detector 6: system_hash
# ---------------------------------------------------------------------------


def test_system_hash_excludes_record_hash_with_hex_values():
    sampled = ["a3f5b9c2d4e6f7a8b1c2d3e4f5a6b7c8"] * 20  # 32-hex
    profile = _profile(cardinality_ratio=1.0, dtype="Utf8")
    result = detect_system_hash("record_hash", sampled, profile)
    assert result is not None
    assert result.detector == "system_hash"
    assert result.evidence["value_shape"] == "hex"


def test_system_hash_excludes_checksum_short_form():
    sampled = ["a3f5b9c2d4e6f7a8"] * 20  # 16-hex
    profile = _profile(cardinality_ratio=1.0, dtype="Utf8")
    result = detect_system_hash("file_checksum", sampled, profile)
    assert result is not None


def test_system_hash_passes_low_cardinality_column():
    """A 'hash' column with cardinality 0.5 isn't a hash -- it's some
    other category."""
    profile = _profile(cardinality_ratio=0.5)
    assert detect_system_hash("hash_bucket", ["a"] * 20, profile) is None


def test_system_hash_passes_name_match_but_non_hex_values():
    """Column named 'fingerprint' with plain text values isn't a hash."""
    sampled = ["alice smith"] * 20
    profile = _profile(cardinality_ratio=1.0, dtype="Utf8")
    assert detect_system_hash("fingerprint", sampled, profile) is None


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def _all_six_poisoned_df() -> pl.DataFrame:
    """A synthetic frame with exactly one poisoned column per detector
    plus one clean identity column. The clean column must NOT be
    excluded; the six poisoned ones must each fire their respective
    detector."""
    n = 100
    import datetime
    return pl.DataFrame({
        # Clean identity column -- must NOT be excluded.
        "first_name": [f"name_{i}" for i in range(n)],
        # 1. audit_column
        "created_at": [
            datetime.datetime(2026, 1, 1) + datetime.timedelta(seconds=i)
            for i in range(n)
        ],
        # 2. foreign_system_id
        "external_id": [f"ext_{i:08d}" for i in range(n)],
        # 3. sentinel_values (40% zeros, well above 10%)
        "phone": ["555-1234"] * 60 + ["0000000000"] * 40,
        # 4. soft_delete_flag
        "is_active": [True] * 70 + [False] * 30,
        # 5. free_text_notes
        "description": ["a" * 100 for _ in range(n)],
        # 6. system_hash
        "record_hash": [f"{i:032x}" for i in range(n)],
    })


def test_orchestrator_excludes_all_six_categories_on_poisoned_frame():
    """End-to-end: a frame with one column per poisoned category
    produces exactly six exclusions, one per detector."""
    df = _all_six_poisoned_df()
    excluded = detect_autoconfig_exclusions(df)
    excluded_cols = {ec.column for ec in excluded}

    assert "first_name" not in excluded_cols, "clean column must NOT be excluded"

    expected_pairs = {
        "created_at": "audit_column",
        "external_id": "foreign_system_id",
        "phone": "sentinel_values",
        "is_active": "soft_delete_flag",
        "description": "free_text_notes",
        "record_hash": "system_hash",
    }
    for col, expected_detector in expected_pairs.items():
        matches = [ec for ec in excluded if ec.column == col]
        assert len(matches) == 1, f"{col} should be excluded exactly once"
        assert matches[0].detector == expected_detector, (
            f"{col}: expected detector={expected_detector}, "
            f"got {matches[0].detector}"
        )


def test_orchestrator_returns_empty_for_clean_dataset():
    """A pristine person-shaped frame produces no exclusions."""
    df = pl.DataFrame({
        "first_name": ["Alice", "Bob", "Carol"],
        "last_name": ["Smith", "Jones", "Doe"],
        "city": ["NYC", "LA", "SF"],
    })
    assert detect_autoconfig_exclusions(df) == []


def test_orchestrator_force_exclude_adds_extra_columns():
    """Caller-supplied force_exclude shows up as exclusions with the
    user_force_exclude detector tag."""
    df = pl.DataFrame({
        "first_name": ["Alice", "Bob"],
        "internal_only_col": ["x", "y"],
    })
    excluded = detect_autoconfig_exclusions(
        df, force_exclude=["internal_only_col"],
    )
    assert len(excluded) == 1
    assert excluded[0].column == "internal_only_col"
    assert excluded[0].detector == "user_force_exclude"


def test_orchestrator_force_include_rescues_auto_detected_exclusion():
    """force_include wins over auto-detection. Pattern: a legitimate
    'email_hash' column for PPRL where the hash IS the identifier --
    the user opts back in."""
    df = pl.DataFrame({
        "email_hash": [f"{i:032x}" for i in range(100)],
        "first_name": [f"n{i}" for i in range(100)],
    })
    # Without rescue, email_hash gets the system_hash exclusion.
    auto = detect_autoconfig_exclusions(df)
    assert any(ec.column == "email_hash" for ec in auto)

    # With rescue, it disappears from the list entirely.
    with_rescue = detect_autoconfig_exclusions(
        df, force_include=["email_hash"],
    )
    assert all(ec.column != "email_hash" for ec in with_rescue)


def test_orchestrator_force_include_overrides_force_exclude():
    """When the caller passes a column in BOTH force_exclude and
    force_include, force_include wins. Lets user configs override an
    inherited dataset-level exclusion."""
    df = pl.DataFrame({"some_col": ["a", "b", "c"]})
    excluded = detect_autoconfig_exclusions(
        df,
        force_exclude=["some_col"],
        force_include=["some_col"],
    )
    assert excluded == []


def test_orchestrator_skip_columns_never_appear_in_output():
    """Internal bookkeeping columns (__row_id__, __source__) shouldn't
    even be inspected. skip_columns is a stronger guarantee than
    force_include: skipped columns cannot show up regardless of any
    detector firing."""
    df = pl.DataFrame({
        "__row_id__": [0, 1, 2, 3],
        "__source__": ["x"] * 4,
        "first_name": ["Alice", "Bob", "Carol", "Dave"],
    })
    excluded = detect_autoconfig_exclusions(
        df, skip_columns={"__row_id__", "__source__"},
    )
    assert all(ec.column not in {"__row_id__", "__source__"} for ec in excluded)


def test_orchestrator_each_column_excluded_at_most_once():
    """First-detector-wins: a column can match multiple detectors by
    name pattern (e.g. ``is_deleted_at`` matches lifecycle AND audit
    suffix), but the orchestrator only emits one ExcludedColumn per
    column. The 6-categories test above already proves each detector
    fires for the right column; this test just locks in that no
    column gets double-excluded under the first-wins rule."""
    df = _all_six_poisoned_df()
    excluded = detect_autoconfig_exclusions(df)
    columns_in_output = [ec.column for ec in excluded]
    assert len(columns_in_output) == len(set(columns_in_output)), (
        "each column must appear at most once in the exclusion list; "
        f"got duplicates in {columns_in_output}"
    )


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
