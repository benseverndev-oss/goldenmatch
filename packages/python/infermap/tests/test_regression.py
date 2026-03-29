"""Regression tests, edge cases, and additional fixture coverage for infermap."""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import polars as pl
import pytest

import infermap
from infermap.config import from_config
from infermap.engine import MapEngine
from infermap.providers import extract_schema
from infermap.scorers import AliasScorer, ExactScorer
from infermap.scorers.pattern_type import classify_field
from infermap.types import FieldInfo, MapResult, ScorerResult

FIXTURES_DIR = Path(__file__).parent / "fixtures"
CRM_EXPORT_CSV = FIXTURES_DIR / "crm_export.csv"
CANONICAL_CUSTOMERS_CSV = FIXTURES_DIR / "canonical_customers.csv"
HEALTHCARE_CSV = FIXTURES_DIR / "healthcare_hl7.csv"
AMBIGUOUS_CSV = FIXTURES_DIR / "ambiguous.csv"


# ---------------------------------------------------------------------------
# 1. test_healthcare_columns: DOB field classifies as date_iso
# ---------------------------------------------------------------------------

def test_healthcare_columns():
    """Extract schema from healthcare_hl7.csv and verify DOB classifies as date_iso."""
    schema = extract_schema(HEALTHCARE_CSV)

    assert schema is not None
    field_names = [f.name for f in schema.fields]
    assert "DOB" in field_names, f"Expected DOB in fields, got: {field_names}"

    dob_field = next(f for f in schema.fields if f.name == "DOB")
    # Sample values should be loaded
    assert len(dob_field.sample_values) > 0, "DOB field should have sample values"

    # classify_field should return date_iso
    detected_type = classify_field(dob_field)
    assert detected_type == "date_iso", (
        f"Expected DOB to classify as 'date_iso', got: {detected_type!r}. "
        f"Sample values: {dob_field.sample_values}"
    )


# ---------------------------------------------------------------------------
# 2. test_ambiguous_columns_dont_crash: mapping ambiguous to canonical
# ---------------------------------------------------------------------------

def test_ambiguous_columns_dont_crash():
    """Map ambiguous.csv to canonical_customers.csv without error; MapResult returned."""
    engine = MapEngine()
    result = engine.map(AMBIGUOUS_CSV, CANONICAL_CUSTOMERS_CSV)

    assert isinstance(result, MapResult), f"Expected MapResult, got: {type(result)}"
    # Just verify it ran without crashing — ambiguous columns may produce 0 mappings
    assert result.mappings is not None
    assert result.unmapped_source is not None


# ---------------------------------------------------------------------------
# 3. test_to_config_then_from_config: full roundtrip
# ---------------------------------------------------------------------------

def test_to_config_then_from_config(tmp_path):
    """Map CSVs, save config, reload with from_config, verify same mappings."""
    engine = MapEngine()
    result = engine.map(CRM_EXPORT_CSV, CANONICAL_CUSTOMERS_CSV)

    assert len(result.mappings) > 0, "Expected at least one mapping from CRM -> canonical"

    config_path = str(tmp_path / "mapping_roundtrip.yaml")
    result.to_config(config_path)

    loaded = from_config(config_path)

    assert isinstance(loaded, MapResult)
    assert len(loaded.mappings) == len(result.mappings), (
        f"Roundtrip mapping count mismatch: original={len(result.mappings)}, "
        f"loaded={len(loaded.mappings)}"
    )

    original_pairs = {(m.source, m.target) for m in result.mappings}
    loaded_pairs = {(m.source, m.target) for m in loaded.mappings}
    assert original_pairs == loaded_pairs, (
        f"Roundtrip mapping pairs differ:\n"
        f"  original: {original_pairs}\n"
        f"  loaded:   {loaded_pairs}"
    )


# ---------------------------------------------------------------------------
# 4. test_custom_scorer_exception_handled: exception in scorer doesn't crash engine
# ---------------------------------------------------------------------------

def test_custom_scorer_exception_handled():
    """A scorer that raises ValueError is handled gracefully; no crash."""

    class BrokenScorer:
        name = "BrokenScorer"
        weight = 1.0

        def score(self, source: FieldInfo, target: FieldInfo) -> ScorerResult | None:
            raise ValueError("Intentional test error from BrokenScorer")

    src_df = pl.DataFrame({"first_name": ["Alice", "Bob"], "email": ["a@b.com", "c@d.com"]})
    tgt_df = pl.DataFrame({"first_name": ["Carol"], "email": ["e@f.com"]})

    # BrokenScorer + ExactScorer + AliasScorer (3 scorers — BrokenScorer will raise)
    scorers = [BrokenScorer(), ExactScorer(), AliasScorer()]
    engine = MapEngine(scorers=scorers)

    # Should not raise — engine catches scorer exceptions internally
    result = engine.map(src_df, tgt_df)
    assert isinstance(result, MapResult)


# ---------------------------------------------------------------------------
# 5. test_zero_row_db_table: SQLite table with no rows
# ---------------------------------------------------------------------------

def test_zero_row_db_table(tmp_path):
    """Create SQLite table with no rows; extract schema; fields exist with empty samples."""
    db_path = str(tmp_path / "empty.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE patients (patient_id INTEGER, name TEXT, dob DATE, active BOOLEAN)"
    )
    conn.commit()
    conn.close()

    uri = f"sqlite:///{db_path}"
    schema = extract_schema(uri, table="patients")

    assert schema is not None
    field_names = [f.name for f in schema.fields]
    assert "patient_id" in field_names
    assert "name" in field_names
    assert "dob" in field_names

    for f in schema.fields:
        assert f.sample_values == [], (
            f"Expected empty samples for zero-row table, field {f.name!r} has: {f.sample_values}"
        )


# ---------------------------------------------------------------------------
# 6. test_top_level_map_convenience: infermap.map() convenience function
# ---------------------------------------------------------------------------

def test_top_level_map_convenience():
    """infermap.map(crm, canonical) works and returns a MapResult with mappings."""
    crm_df = pl.read_csv(CRM_EXPORT_CSV)
    canonical_df = pl.read_csv(CANONICAL_CUSTOMERS_CSV)

    result = infermap.map(crm_df, canonical_df)

    assert isinstance(result, MapResult), f"Expected MapResult, got: {type(result)}"
    assert len(result.mappings) > 0, (
        f"Expected at least one mapping from top-level infermap.map(), got: {result.mappings}"
    )

    mapped = {m.source: m.target for m in result.mappings}
    assert mapped.get("fname") == "first_name", (
        f"Expected fname -> first_name via infermap.map(), got: {mapped}"
    )
