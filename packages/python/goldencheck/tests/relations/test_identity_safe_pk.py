"""Tests for IdentitySafePkProfiler (closes goldenmatch #207).

The profiler warns when no column in the dataset qualifies as a
stable PK candidate. Downstream consumers (notably goldenmatch's
Identity Graph) fall back to a payload-hash record_id when no
source_pk_column is configured, which silently collides on
duplicate raw rows.
"""
from __future__ import annotations

import polars as pl
import pytest
from goldencheck.models.finding import Severity
from goldencheck.relations.identity_safe_pk import IdentitySafePkProfiler


@pytest.fixture
def profiler() -> IdentitySafePkProfiler:
    return IdentitySafePkProfiler()


def test_clean_pk_column_no_warning(profiler: IdentitySafePkProfiler) -> None:
    """A column named 'id' that's fully unique + non-null is a viable PK."""
    df = pl.DataFrame({
        "id": [1, 2, 3, 4, 5],
        "name": ["Alice", "Bob", "Carol", "Dave", "Eve"],
        "email": ["a@x", "b@x", "c@x", "d@x", "e@x"],
    })
    findings = profiler.profile(df)
    assert findings == []


def test_uuid_string_pk_no_warning(profiler: IdentitySafePkProfiler) -> None:
    """String UUIDs in a 'guid' column qualify as PK."""
    df = pl.DataFrame({
        "guid": ["a1", "b2", "c3"],
        "value": [10, 20, 30],
    })
    findings = profiler.profile(df)
    assert findings == []


def test_no_pk_column_emits_warning(profiler: IdentitySafePkProfiler) -> None:
    """No column qualifies -> dataset-level WARNING."""
    df = pl.DataFrame({
        "first_name": ["Alice", "Alice", "Bob"],  # duplicates
        "last_name": ["Smith", "Smith", "Jones"],
        "city": ["NYC", "NYC", "LA"],
    })
    findings = profiler.profile(df)
    assert len(findings) == 1
    f = findings[0]
    assert f.severity == Severity.WARNING
    assert f.check == "identity_safe_pk"
    assert f.column == "__dataset__"
    assert "stable PK" in f.message or "PK" in f.message
    assert "source_pk_column" in f.suggestion


def test_named_pk_column_with_nulls_emits_specific_warning(
    profiler: IdentitySafePkProfiler,
) -> None:
    """A column named like a PK ('customer_id') but with nulls -> WARNING
    anchored to that column, not the generic dataset-level finding."""
    df = pl.DataFrame({
        "customer_id": [1, 2, None, 4],
        "name": ["A", "B", "C", "D"],
    })
    findings = profiler.profile(df)
    assert len(findings) == 1
    f = findings[0]
    assert f.severity == Severity.WARNING
    assert f.column == "customer_id"
    assert "null" in f.message.lower()


def test_named_pk_column_with_duplicates_emits_specific_warning(
    profiler: IdentitySafePkProfiler,
) -> None:
    """'record_id' that's not unique -> column-specific warning.

    Other columns intentionally have duplicates too so the
    record_id-named-but-broken column is the only viable signal.
    """
    df = pl.DataFrame({
        "record_id": [1, 1, 2, 3],  # duplicate
        "city": ["NYC", "LA", "NYC", "LA"],  # duplicates
    })
    findings = profiler.profile(df)
    assert len(findings) == 1
    f = findings[0]
    assert f.column == "record_id"
    assert "non-unique" in f.message or "unique" in f.message.lower()


def test_value_column_unique_does_not_qualify(
    profiler: IdentitySafePkProfiler,
) -> None:
    """A unique 'email' column doesn't qualify as a stable PK
    (emails are editable; that's value data, not identity data)."""
    df = pl.DataFrame({
        "email": ["a@x", "b@x", "c@x"],
        "name": ["Alice", "Bob", "Carol"],
    })
    findings = profiler.profile(df)
    # No id/uuid column; email is a value column. Should warn.
    assert len(findings) == 1
    assert findings[0].check == "identity_safe_pk"


def test_float_column_not_eligible_pk(
    profiler: IdentitySafePkProfiler,
) -> None:
    """Unique floats don't qualify (float equality is unsafe)."""
    df = pl.DataFrame({
        "score": [0.1, 0.2, 0.3, 0.4],
        "label": ["a", "b", "c", "d"],
    })
    findings = profiler.profile(df)
    # 'label' is unique + non-null + not a value column -> qualifies.
    # No warning expected.
    assert findings == []


def test_boolean_column_not_eligible_pk(
    profiler: IdentitySafePkProfiler,
) -> None:
    """A column with only True/False can't be a unique PK at scale."""
    df = pl.DataFrame({
        "is_active": [True, False, True, False],  # not unique
        "city": ["NYC", "LA", "NYC", "LA"],
    })
    findings = profiler.profile(df)
    # No PK candidate; expect dataset-level warning.
    assert len(findings) == 1
    assert findings[0].check == "identity_safe_pk"


def test_empty_dataframe_no_findings(profiler: IdentitySafePkProfiler) -> None:
    """Empty DF -> no findings (degenerate case; not our problem)."""
    df = pl.DataFrame()
    findings = profiler.profile(df)
    assert findings == []


def test_multiple_pk_candidates_no_warning(
    profiler: IdentitySafePkProfiler,
) -> None:
    """When several columns qualify (e.g. both 'id' and 'sku'), still
    no warning -- any one viable PK is enough."""
    df = pl.DataFrame({
        "id": [1, 2, 3],
        "sku": ["a", "b", "c"],
        "name": ["A", "B", "C"],
    })
    findings = profiler.profile(df)
    assert findings == []
