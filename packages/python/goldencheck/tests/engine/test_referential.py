"""Tests for cross-file referential-integrity checks."""
from __future__ import annotations

import polars as pl
from goldencheck.engine.referential import (
    auto_detect_mappings,
    check_referential_integrity,
)


def _parent() -> pl.DataFrame:
    return pl.DataFrame({"id": [1, 2, 3], "name": ["a", "b", "c"]})


def test_clean_fk_is_info() -> None:
    child = pl.DataFrame({"customer_id": [1, 2, 2, 3]})
    findings = check_referential_integrity(
        child, _parent(), [("customer_id", "id")]
    )
    assert len(findings) == 1
    assert findings[0].severity.name == "INFO"
    assert findings[0].metadata["orphan_rows"] == 0
    assert findings[0].metadata["cardinality"] == "N:1"


def test_orphans_flagged_as_error() -> None:
    child = pl.DataFrame({"customer_id": [1, 2, 99, 3, 99]})  # 99 has no parent
    findings = check_referential_integrity(
        child, _parent(), [("customer_id", "id")]
    )
    f = findings[0]
    assert f.severity.name == "ERROR"
    assert f.metadata["orphan_rows"] == 2
    assert f.metadata["distinct_orphans"] == 1
    assert "99" in f.sample_values


def test_nulls_ignored_in_fk() -> None:
    child = pl.DataFrame({"customer_id": [1, None, 2]})
    findings = check_referential_integrity(child, _parent(), [("customer_id", "id")])
    assert findings[0].metadata["orphan_rows"] == 0


def test_auto_detect_matches_unique_parent_key() -> None:
    parent = _parent()
    child = pl.DataFrame({"id": [1, 2], "extra": [9, 9]})
    # parent.id is a unique key and 'id' exists in child -> detected.
    assert auto_detect_mappings(child, parent) == [("id", "id")]
    # parent.name is unique too here; but child has no 'name' column.
    assert all(c != "name" for c, _ in auto_detect_mappings(child, parent))


def test_auto_detect_skips_non_key_parent_column() -> None:
    parent = pl.DataFrame({"id": [1, 1, 2]})  # 'id' not unique -> not a key
    child = pl.DataFrame({"id": [1, 2]})
    assert auto_detect_mappings(child, parent) == []
