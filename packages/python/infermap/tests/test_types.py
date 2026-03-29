"""Tests for infermap core types."""
from __future__ import annotations

import json
import os
import tempfile

import polars as pl
import pytest

from infermap.errors import ApplyError
from infermap.types import (
    FieldInfo,
    FieldMapping,
    MapResult,
    SchemaInfo,
    ScorerResult,
)
from tests.conftest import make_field, make_schema


# ---------------------------------------------------------------------------
# FieldInfo
# ---------------------------------------------------------------------------


class TestFieldInfo:
    def test_defaults(self):
        fi = FieldInfo(name="my_field")
        assert fi.name == "my_field"
        assert fi.dtype == "string"
        assert fi.sample_values == []
        assert fi.null_rate == 0.0
        assert fi.unique_rate == 0.0
        assert fi.value_count == 0
        assert fi.metadata == {}

    def test_valid_dtype_preserved(self):
        for dtype in ("string", "integer", "float", "boolean", "date", "datetime"):
            fi = FieldInfo(name="x", dtype=dtype)
            assert fi.dtype == dtype

    def test_invalid_dtype_defaults_to_string(self):
        fi = FieldInfo(name="x", dtype="uuid")
        assert fi.dtype == "string"

    def test_empty_string_dtype_defaults_to_string(self):
        fi = FieldInfo(name="x", dtype="")
        assert fi.dtype == "string"

    def test_make_field_helper(self):
        fi = make_field("email", dtype="string", value_count=10)
        assert fi.name == "email"
        assert fi.value_count == 10


# ---------------------------------------------------------------------------
# SchemaInfo
# ---------------------------------------------------------------------------


class TestSchemaInfo:
    def test_make_schema_helper(self):
        schema = make_schema(["fname", "lname", "email"], source_name="crm")
        assert len(schema.fields) == 3
        assert schema.source_name == "crm"
        assert schema.fields[0].name == "fname"

    def test_required_fields_default_empty(self):
        schema = SchemaInfo(fields=[])
        assert schema.required_fields == []


# ---------------------------------------------------------------------------
# ScorerResult
# ---------------------------------------------------------------------------


class TestScorerResult:
    def test_score_in_range_preserved(self):
        sr = ScorerResult(score=0.75, reasoning="good match")
        assert sr.score == 0.75

    def test_score_clamped_above_one(self):
        sr = ScorerResult(score=1.5, reasoning="too high")
        assert sr.score == 1.0

    def test_score_clamped_below_zero(self):
        sr = ScorerResult(score=-0.3, reasoning="negative")
        assert sr.score == 0.0

    def test_score_exactly_zero_and_one(self):
        assert ScorerResult(score=0.0, reasoning="").score == 0.0
        assert ScorerResult(score=1.0, reasoning="").score == 1.0


# ---------------------------------------------------------------------------
# FieldMapping
# ---------------------------------------------------------------------------


class TestFieldMapping:
    def test_basic_construction(self):
        fm = FieldMapping(source="fname", target="first_name", confidence=0.9)
        assert fm.source == "fname"
        assert fm.target == "first_name"
        assert fm.confidence == 0.9
        assert fm.breakdown == {}
        assert fm.reasoning == ""

    def test_with_breakdown(self):
        sr = ScorerResult(score=0.85, reasoning="alias match")
        fm = FieldMapping(
            source="fname",
            target="first_name",
            confidence=0.85,
            breakdown={"alias": sr},
            reasoning="alias scorer matched",
        )
        assert fm.breakdown["alias"].score == 0.85


# ---------------------------------------------------------------------------
# MapResult.report()
# ---------------------------------------------------------------------------


class TestMapResultReport:
    def _make_result(self) -> MapResult:
        sr = ScorerResult(score=0.9, reasoning="exact match")
        fm = FieldMapping(
            source="fname",
            target="first_name",
            confidence=0.9,
            breakdown={"exact": sr},
            reasoning="exact scorer",
        )
        return MapResult(
            mappings=[fm],
            unmapped_source=["extra_col"],
            unmapped_target=["middle_name"],
            warnings=["low confidence on tel"],
        )

    def test_report_structure(self):
        result = self._make_result()
        report = result.report()
        assert "mappings" in report
        assert "unmapped_source" in report
        assert "unmapped_target" in report
        assert "warnings" in report

    def test_report_mapping_fields(self):
        result = self._make_result()
        m = result.report()["mappings"][0]
        assert m["source"] == "fname"
        assert m["target"] == "first_name"
        assert m["confidence"] == 0.9
        assert "exact" in m["breakdown"]
        assert m["breakdown"]["exact"]["score"] == 0.9
        assert m["reasoning"] == "exact scorer"

    def test_report_unmapped_lists(self):
        result = self._make_result()
        report = result.report()
        assert report["unmapped_source"] == ["extra_col"]
        assert report["unmapped_target"] == ["middle_name"]
        assert report["warnings"] == ["low confidence on tel"]

    def test_report_confidence_rounded(self):
        fm = FieldMapping(source="a", target="b", confidence=0.123456)
        result = MapResult(mappings=[fm])
        assert result.report()["mappings"][0]["confidence"] == 0.123


# ---------------------------------------------------------------------------
# MapResult.to_json()
# ---------------------------------------------------------------------------


class TestMapResultToJson:
    def test_to_json_returns_valid_json(self):
        fm = FieldMapping(source="a", target="b", confidence=0.8)
        result = MapResult(mappings=[fm])
        json_str = result.to_json()
        parsed = json.loads(json_str)
        assert parsed["mappings"][0]["source"] == "a"

    def test_to_json_matches_report(self):
        fm = FieldMapping(source="x", target="y", confidence=0.5)
        result = MapResult(mappings=[fm])
        assert json.loads(result.to_json()) == result.report()


# ---------------------------------------------------------------------------
# MapResult.apply() — Polars
# ---------------------------------------------------------------------------


class TestMapResultApplyPolars:
    def _make_result(self) -> MapResult:
        return MapResult(
            mappings=[
                FieldMapping(source="fname", target="first_name", confidence=0.95),
                FieldMapping(source="lname", target="last_name", confidence=0.95),
            ]
        )

    def test_apply_renames_polars_columns(self):
        df = pl.DataFrame({"fname": ["John"], "lname": ["Doe"], "email": ["j@e.com"]})
        result = self._make_result()
        out = result.apply(df)
        assert "first_name" in out.columns
        assert "last_name" in out.columns
        assert "email" in out.columns
        assert "fname" not in out.columns

    def test_apply_returns_polars_dataframe(self):
        df = pl.DataFrame({"fname": ["A"], "lname": ["B"]})
        out = self._make_result().apply(df)
        assert isinstance(out, pl.DataFrame)

    def test_apply_raises_on_missing_source_column(self):
        df = pl.DataFrame({"fname": ["John"]})  # missing lname
        result = self._make_result()
        with pytest.raises(ApplyError, match="lname"):
            result.apply(df)


# ---------------------------------------------------------------------------
# MapResult.apply() — Pandas
# ---------------------------------------------------------------------------


class TestMapResultApplyPandas:
    def test_apply_renames_pandas_columns(self):
        pd = pytest.importorskip("pandas")
        df = pd.DataFrame({"fname": ["John"], "lname": ["Doe"]})
        result = MapResult(
            mappings=[FieldMapping(source="fname", target="first_name", confidence=0.9)]
        )
        out = result.apply(df)
        assert "first_name" in out.columns
        assert "fname" not in out.columns

    def test_apply_preserves_pandas_type(self):
        pd = pytest.importorskip("pandas")
        df = pd.DataFrame({"fname": ["Alice"], "lname": ["Smith"]})
        result = MapResult(
            mappings=[
                FieldMapping(source="fname", target="first_name", confidence=0.9),
                FieldMapping(source="lname", target="last_name", confidence=0.9),
            ]
        )
        out = result.apply(df)
        assert type(out).__name__ == "DataFrame"
        assert hasattr(out, "iloc")

    def test_apply_raises_on_missing_column_pandas(self):
        pd = pytest.importorskip("pandas")
        df = pd.DataFrame({"fname": ["Alice"]})
        result = MapResult(
            mappings=[FieldMapping(source="missing_col", target="x", confidence=0.5)]
        )
        with pytest.raises(ApplyError, match="missing_col"):
            result.apply(df)


# ---------------------------------------------------------------------------
# MapResult.to_config()
# ---------------------------------------------------------------------------


class TestMapResultToConfig:
    def _make_result(self) -> MapResult:
        return MapResult(
            mappings=[
                FieldMapping(source="fname", target="first_name", confidence=0.95),
                FieldMapping(source="email_addr", target="email", confidence=0.88),
            ],
            unmapped_source=["tel"],
            unmapped_target=["phone"],
        )

    def test_to_config_writes_yaml(self):
        import yaml

        result = self._make_result()
        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w") as f:
            path = f.name
        try:
            result.to_config(path)
            with open(path) as f:
                content = f.read()
            data = yaml.safe_load(content)
            assert data is not None
        finally:
            os.unlink(path)

    def test_to_config_version_field(self):
        import yaml

        result = self._make_result()
        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w") as f:
            path = f.name
        try:
            result.to_config(path)
            with open(path) as f:
                data = yaml.safe_load(f)
            assert data["version"] == "1"
        finally:
            os.unlink(path)

    def test_to_config_roundtrip_mappings(self):
        import yaml

        result = self._make_result()
        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w") as f:
            path = f.name
        try:
            result.to_config(path)
            with open(path) as f:
                data = yaml.safe_load(f)
            sources = [m["source"] for m in data["mappings"]]
            assert "fname" in sources
            assert "email_addr" in sources
            assert data["unmapped_source"] == ["tel"]
            assert data["unmapped_target"] == ["phone"]
        finally:
            os.unlink(path)

    def test_to_config_has_generated_comment(self):
        result = self._make_result()
        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w") as f:
            path = f.name
        try:
            result.to_config(path)
            with open(path) as f:
                first_line = f.readline()
            assert "infermap" in first_line
        finally:
            os.unlink(path)
