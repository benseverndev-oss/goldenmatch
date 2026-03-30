"""End-to-end integration tests — comprehensive real-world validation."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import polars as pl
import pytest
import yaml

import infermap
from infermap.types import MapResult, ScorerResult
from tests.conftest import FIXTURES_DIR as FIXTURES


class TestFileToFileMapping:
    """Test the primary use case: map messy CSV to clean CSV."""

    def test_crm_to_canonical(self):
        result = infermap.map(
            str(FIXTURES / "crm_export.csv"),
            str(FIXTURES / "canonical_customers.csv"),
        )
        assert isinstance(result, MapResult)
        assert len(result.mappings) > 0

        mapped = {m.source: m.target for m in result.mappings}
        # These should map via AliasScorer
        assert mapped.get("fname") == "first_name", f"fname mapping: {mapped}"
        assert mapped.get("lname") == "last_name", f"lname mapping: {mapped}"

    def test_confidence_scores_are_reasonable(self):
        result = infermap.map(
            str(FIXTURES / "crm_export.csv"),
            str(FIXTURES / "canonical_customers.csv"),
        )
        for m in result.mappings:
            assert 0.0 < m.confidence <= 1.0, f"{m.source}->{m.target}: {m.confidence}"

    def test_every_mapping_has_reasoning(self):
        result = infermap.map(
            str(FIXTURES / "crm_export.csv"),
            str(FIXTURES / "canonical_customers.csv"),
        )
        for m in result.mappings:
            assert m.reasoning, f"{m.source}->{m.target} has no reasoning"

    def test_every_mapping_has_breakdown(self):
        result = infermap.map(
            str(FIXTURES / "crm_export.csv"),
            str(FIXTURES / "canonical_customers.csv"),
        )
        for m in result.mappings:
            assert len(m.breakdown) >= 2, (
                f"{m.source}->{m.target} only has {len(m.breakdown)} scorers"
            )


class TestDataFrameMapping:
    """Test in-memory DataFrame mapping."""

    def test_polars_df_mapping(self):
        src = pl.DataFrame({
            "email_addr": ["a@b.com", "x@y.com"],
            "tel": ["555-1234", "555-5678"],
            "zipcode": ["10001", "90210"],
        })
        tgt = pl.DataFrame({
            "email": ["test@test.com"],
            "phone": ["999-9999"],
            "zip_code": ["00000"],
        })
        result = infermap.map(src, tgt)
        mapped = {m.source: m.target for m in result.mappings}
        assert "email_addr" in mapped or "email" in mapped.values()
        assert "tel" in mapped or "phone" in mapped.values()

    def test_apply_produces_correct_columns(self):
        src = pl.DataFrame({"fname": ["John"], "lname": ["Doe"]})
        tgt = pl.DataFrame({"first_name": ["Jane"], "last_name": ["Smith"]})
        result = infermap.map(src, tgt)
        remapped = result.apply(src)
        assert "first_name" in remapped.columns
        assert "last_name" in remapped.columns
        assert "fname" not in remapped.columns

    def test_pandas_roundtrip(self):
        pd = pytest.importorskip("pandas")
        src = pd.DataFrame({"fname": ["John"], "lname": ["Doe"]})
        tgt_pl = pl.DataFrame({"first_name": ["Jane"], "last_name": ["Smith"]})
        result = infermap.map(src, tgt_pl)
        remapped = result.apply(src)
        assert isinstance(remapped, pd.DataFrame)
        assert "first_name" in remapped.columns


class TestDatabaseMapping:
    """Test database source/target mapping."""

    def test_sqlite_as_target(self, tmp_path):
        db_path = tmp_path / "target.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE customers (first_name TEXT, last_name TEXT, email TEXT, phone TEXT)")
        conn.execute("INSERT INTO customers VALUES ('Alice', 'Smith', 'alice@test.com', '555-0001')")
        conn.execute("INSERT INTO customers VALUES ('Bob', 'Jones', 'bob@test.com', '555-0002')")
        conn.commit()
        conn.close()

        result = infermap.map(
            str(FIXTURES / "crm_export.csv"),
            f"sqlite:///{db_path}",
            table="customers",
        )
        mapped = {m.source: m.target for m in result.mappings}
        assert mapped.get("fname") == "first_name"
        assert mapped.get("email_addr") == "email"

    def test_duckdb_as_target(self, tmp_path):
        duckdb = pytest.importorskip("duckdb")
        db_path = str(tmp_path / "target.duckdb")
        conn = duckdb.connect(db_path)
        conn.execute("CREATE TABLE users (first_name VARCHAR, last_name VARCHAR, email VARCHAR)")
        conn.execute("INSERT INTO users VALUES ('Alice', 'Smith', 'alice@test.com')")
        conn.close()

        result = infermap.map(
            str(FIXTURES / "crm_export.csv"),
            f"duckdb:///{db_path}",
            table="users",
        )
        mapped = {m.source: m.target for m in result.mappings}
        assert mapped.get("fname") == "first_name"


class TestConfigRoundtrip:
    """Test save/load mapping cycle."""

    def test_full_roundtrip(self, tmp_path):
        # Map
        result = infermap.map(
            str(FIXTURES / "crm_export.csv"),
            str(FIXTURES / "canonical_customers.csv"),
        )
        original_mappings = [(m.source, m.target) for m in result.mappings]

        # Save
        config_path = str(tmp_path / "mapping.yaml")
        result.to_config(config_path)

        # Verify YAML structure
        with open(config_path) as f:
            data = yaml.safe_load(f)
        assert data["version"] == "1"
        assert len(data["mappings"]) == len(original_mappings)

        # Reload
        loaded = infermap.from_config(config_path)
        loaded_mappings = [(m.source, m.target) for m in loaded.mappings]
        assert loaded_mappings == original_mappings

        # Apply loaded config to a new DataFrame
        df = pl.read_csv(str(FIXTURES / "crm_export.csv"))
        remapped = loaded.apply(df)
        for _, target in original_mappings:
            assert target in remapped.columns


class TestReportAndJSON:
    """Test report output formats."""

    def test_report_structure(self):
        result = infermap.map(
            str(FIXTURES / "crm_export.csv"),
            str(FIXTURES / "canonical_customers.csv"),
        )
        report = result.report()
        assert "mappings" in report
        assert "unmapped_source" in report
        assert "unmapped_target" in report
        assert "warnings" in report

    def test_json_is_valid(self):
        result = infermap.map(
            str(FIXTURES / "crm_export.csv"),
            str(FIXTURES / "canonical_customers.csv"),
        )
        j = result.to_json()
        parsed = json.loads(j)
        assert len(parsed["mappings"]) > 0

    def test_breakdown_has_scorer_names(self):
        result = infermap.map(
            str(FIXTURES / "crm_export.csv"),
            str(FIXTURES / "canonical_customers.csv"),
        )
        report = result.report()
        for m in report["mappings"]:
            for scorer_name, details in m["breakdown"].items():
                assert "score" in details
                assert "reasoning" in details


class TestSchemaFileOverlay:
    """Test schema definition file as extra signal."""

    def test_schema_file_with_aliases(self, tmp_path):
        schema_file = tmp_path / "target.yaml"
        schema_file.write_text(yaml.dump({
            "fields": [
                {"name": "email", "type": "string", "aliases": ["email_addr", "e_mail"], "required": True},
                {"name": "phone", "type": "string", "aliases": ["tel", "telephone"]},
            ]
        }))

        src = pl.DataFrame({
            "email_addr": ["a@b.com"],
            "tel": ["555-1234"],
        })
        tgt = pl.DataFrame({
            "email": ["x@y.com"],
            "phone": ["999-9999"],
        })
        result = infermap.map(src, tgt, schema_file=str(schema_file))
        mapped = {m.source: m.target for m in result.mappings}
        assert mapped.get("email_addr") == "email"
        assert mapped.get("tel") == "phone"


class TestRequiredFields:
    """Test required field warnings."""

    def test_missing_required_generates_warning(self):
        src = pl.DataFrame({"x": [1], "y": [2]})
        tgt = pl.DataFrame({"email": ["a@b.com"], "phone": ["555"]})
        result = infermap.map(src, tgt, required=["email"])
        assert any("email" in w for w in result.warnings)

    def test_satisfied_required_no_warning(self):
        src = pl.DataFrame({"email": ["a@b.com"]})
        tgt = pl.DataFrame({"email": ["x@y.com"]})
        result = infermap.map(src, tgt, required=["email"])
        assert not any("email" in w for w in result.warnings)


class TestCustomScorer:
    """Test plugin scorer system."""

    def test_custom_scorer_contributes(self):
        @infermap.scorer(name="_e2e_custom", weight=0.6)
        def custom(source, target):
            if "test" in source.name and "test" in target.name:
                return ScorerResult(score=1.0, reasoning="both have 'test'")
            return ScorerResult(score=0.0, reasoning="no match")

        src = pl.DataFrame({"test_a": [1]})
        tgt = pl.DataFrame({"test_b": [2]})
        engine = infermap.MapEngine(
            scorers=infermap.default_scorers() + [custom],
            min_confidence=0.01,
        )
        result = engine.map(src, tgt)
        # Custom scorer should have contributed
        if result.mappings:
            assert "_e2e_custom" in result.mappings[0].breakdown

        # Clean up registry
        from infermap.scorers import _REGISTRY
        _REGISTRY.pop("_e2e_custom", None)


class TestCLI:
    """Test CLI commands via CliRunner."""

    def test_map_command(self):
        from typer.testing import CliRunner
        from infermap.cli import app

        runner = CliRunner()
        result = runner.invoke(app, [
            "map",
            str(FIXTURES / "crm_export.csv"),
            str(FIXTURES / "canonical_customers.csv"),
        ])
        assert result.exit_code == 0
        assert "first_name" in result.stdout  # table format shows target columns

    def test_map_json(self):
        from typer.testing import CliRunner
        from infermap.cli import app

        runner = CliRunner()
        result = runner.invoke(app, [
            "map",
            str(FIXTURES / "crm_export.csv"),
            str(FIXTURES / "canonical_customers.csv"),
            "--format", "json",
        ])
        assert result.exit_code == 0
        parsed = json.loads(result.stdout)
        assert "mappings" in parsed

    def test_inspect_command(self):
        from typer.testing import CliRunner
        from infermap.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["inspect", str(FIXTURES / "crm_export.csv")])
        assert result.exit_code == 0
        assert "fname" in result.stdout

    def test_validate_strict_fails(self, tmp_path):
        from typer.testing import CliRunner
        from infermap.cli import app

        config = tmp_path / "m.yaml"
        config.write_text('version: "1"\nmappings:\n  - source: fname\n    target: first_name\n    confidence: 0.95\n')
        runner = CliRunner()
        result = runner.invoke(app, [
            "validate", str(FIXTURES / "crm_export.csv"),
            "--config", str(config),
            "--strict", "--required", "email,phone",
        ])
        assert result.exit_code == 1


class TestEdgeCases:
    """Test boundary conditions and error handling."""

    def test_empty_dataframe(self):
        src = pl.DataFrame({"a": pl.Series([], dtype=pl.Utf8)})
        tgt = pl.DataFrame({"b": pl.Series([], dtype=pl.Utf8)})
        result = infermap.map(src, tgt)
        assert isinstance(result, MapResult)

    def test_single_column(self):
        src = pl.DataFrame({"email": ["a@b.com"]})
        tgt = pl.DataFrame({"email": ["x@y.com"]})
        result = infermap.map(src, tgt)
        assert len(result.mappings) == 1
        assert result.mappings[0].source == "email"
        assert result.mappings[0].target == "email"
        assert result.mappings[0].confidence > 0.95  # near-perfect match (weighted avg slightly below 1.0)

    def test_no_overlap(self):
        src = pl.DataFrame({"aaa_xxx": [1]})
        tgt = pl.DataFrame({"zzz_yyy": [2]})
        result = infermap.map(src, tgt, required=[])
        # Might map or not depending on fuzzy score, but shouldn't crash
        assert isinstance(result, MapResult)

    def test_many_columns(self):
        """Stress test with 50 columns."""
        src_data = {f"src_col_{i}": [f"val_{i}"] for i in range(50)}
        tgt_data = {f"tgt_col_{i}": [f"val_{i}"] for i in range(50)}
        src = pl.DataFrame(src_data)
        tgt = pl.DataFrame(tgt_data)
        result = infermap.map(src, tgt)
        assert isinstance(result, MapResult)
        assert result.metadata["source_field_count"] == 50
        assert result.metadata["target_field_count"] == 50
