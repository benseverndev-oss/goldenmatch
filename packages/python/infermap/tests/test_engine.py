"""Tests for MapEngine — the orchestrator."""
from __future__ import annotations

from pathlib import Path

import polars as pl

from infermap.engine import MapEngine
from infermap.types import MapResult

FIXTURES_DIR = Path(__file__).parent / "fixtures"
CRM_EXPORT_CSV = FIXTURES_DIR / "crm_export.csv"
CANONICAL_CUSTOMERS_CSV = FIXTURES_DIR / "canonical_customers.csv"


# ---------------------------------------------------------------------------
# 1. csv_to_csv: basic end-to-end mapping
# ---------------------------------------------------------------------------

def test_csv_to_csv():
    """fname (CRM) should map to first_name (canonical customers)."""
    engine = MapEngine()
    result = engine.map(CRM_EXPORT_CSV, CANONICAL_CUSTOMERS_CSV)

    assert isinstance(result, MapResult)
    assert len(result.mappings) > 0

    mapped = {m.source: m.target for m in result.mappings}
    assert mapped.get("fname") == "first_name", (
        f"Expected fname -> first_name, got mappings: {mapped}"
    )


# ---------------------------------------------------------------------------
# 2. dataframe_to_dataframe: Polars DataFrames
# ---------------------------------------------------------------------------

def test_dataframe_to_dataframe():
    """email_address -> email, tel -> phone via Polars DataFrames."""
    src_df = pl.DataFrame({
        "email_address": ["a@b.com", "c@d.com"],
        "tel": ["555-0001", "555-0002"],
    })
    tgt_df = pl.DataFrame({
        "email": ["x@y.com"],
        "phone": ["555-9999"],
    })

    engine = MapEngine()
    result = engine.map(src_df, tgt_df)

    mapped = {m.source: m.target for m in result.mappings}
    assert mapped.get("email_address") == "email", (
        f"Expected email_address -> email, got: {mapped}"
    )
    assert mapped.get("tel") == "phone", (
        f"Expected tel -> phone, got: {mapped}"
    )


# ---------------------------------------------------------------------------
# 3. required_field_warning: missing required field generates a warning
# ---------------------------------------------------------------------------

def test_required_field_warning():
    """If source has just 'x', required=["email"] should generate a warning."""
    src_df = pl.DataFrame({"x": ["hello"]})
    tgt_df = pl.DataFrame({"email": ["a@b.com"], "phone": ["555-1111"]})

    engine = MapEngine(min_confidence=0.3)
    result = engine.map(src_df, tgt_df, required=["email"])

    assert any("email" in w for w in result.warnings), (
        f"Expected warning about 'email', got: {result.warnings}"
    )


# ---------------------------------------------------------------------------
# 4. min_confidence_filtering: unrelated columns with high threshold
# ---------------------------------------------------------------------------

def test_min_confidence_filtering():
    """Completely unrelated column names with min_confidence=0.9 → 0 mappings."""
    src_df = pl.DataFrame({"alpha": [1], "beta": [2]})
    tgt_df = pl.DataFrame({"gamma": [3], "delta": [4]})

    engine = MapEngine(min_confidence=0.9)
    result = engine.map(src_df, tgt_df)

    assert len(result.mappings) == 0, (
        f"Expected 0 mappings but got: {[(m.source, m.target, m.confidence) for m in result.mappings]}"
    )


# ---------------------------------------------------------------------------
# 5. apply_after_map: rename DataFrame columns via MapResult.apply()
# ---------------------------------------------------------------------------

def test_apply_after_map():
    """Map CSVs, then apply result to source DataFrame → columns renamed."""
    engine = MapEngine()
    result = engine.map(CRM_EXPORT_CSV, CANONICAL_CUSTOMERS_CSV)

    # Load source as a Polars DataFrame
    src_df = pl.read_csv(CRM_EXPORT_CSV)
    renamed_df = result.apply(src_df)

    # At least fname should have been renamed to first_name
    mapped = {m.source: m.target for m in result.mappings}
    for src_col, tgt_col in mapped.items():
        assert tgt_col in renamed_df.columns, (
            f"Expected column '{tgt_col}' in renamed DataFrame, columns: {renamed_df.columns}"
        )


# ---------------------------------------------------------------------------
# 6. metadata_includes_timing
# ---------------------------------------------------------------------------

def test_metadata_includes_timing():
    """MapResult.metadata should contain elapsed_seconds."""
    engine = MapEngine()
    result = engine.map(CRM_EXPORT_CSV, CANONICAL_CUSTOMERS_CSV)

    assert "elapsed_seconds" in result.metadata, (
        f"elapsed_seconds not in metadata: {result.metadata}"
    )
    assert result.metadata["elapsed_seconds"] >= 0.0


# ---------------------------------------------------------------------------
# 7. minimum_contributor_threshold: only FuzzyNameScorer fires → filtered out
# ---------------------------------------------------------------------------

def test_minimum_contributor_threshold():
    """When only FuzzyNameScorer is configured (1 scorer), the minimum 2-contributor
    threshold is not met → combined score = 0.0 → filtered out at any min_confidence > 0.

    This validates the engine's guard: a single scorer's opinion is not enough to
    establish a mapping confidence.
    """
    from infermap.scorers import FuzzyNameScorer

    # Use only FuzzyNameScorer — exactly 1 scorer, so contributor count < 2 for all pairs
    # FuzzyNameScorer always returns non-None, but 1 < min_contributors(2) → score 0.0
    src_df = pl.DataFrame({"xyzpqr": ["val1"]})
    tgt_df = pl.DataFrame({"xyzpqs": ["val2"]})

    engine = MapEngine(min_confidence=0.3, scorers=[FuzzyNameScorer()])
    result = engine.map(src_df, tgt_df)

    assert len(result.mappings) == 0, (
        f"Expected 0 mappings due to min-contributor threshold (only 1 scorer), got: "
        f"{[(m.source, m.target, m.confidence) for m in result.mappings]}"
    )
