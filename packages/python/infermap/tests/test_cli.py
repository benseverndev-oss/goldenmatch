"""Tests for infermap CLI commands."""
from __future__ import annotations

from pathlib import Path

import yaml
from typer.testing import CliRunner

from infermap.cli import app

runner = CliRunner()

FIXTURES_DIR = Path(__file__).parent / "fixtures"
CRM_EXPORT_CSV = str(FIXTURES_DIR / "crm_export.csv")
CANONICAL_CUSTOMERS_CSV = str(FIXTURES_DIR / "canonical_customers.csv")


class TestMapCommand:
    def test_map_csv_to_csv(self):
        """map command runs with exit code 0 and shows mapped fields."""
        result = runner.invoke(app, ["map", CRM_EXPORT_CSV, CANONICAL_CUSTOMERS_CSV])
        assert result.exit_code == 0, f"Output:\n{result.output}\nException:\n{result.exception}"
        # Should show at least fname or first_name in the output table
        assert "fname" in result.output or "first_name" in result.output

    def test_map_json_format(self):
        """--format json emits valid JSON with a 'mappings' key."""
        result = runner.invoke(
            app, ["map", CRM_EXPORT_CSV, CANONICAL_CUSTOMERS_CSV, "--format", "json"]
        )
        assert result.exit_code == 0, f"Output:\n{result.output}"
        assert '"mappings"' in result.output

    def test_map_yaml_format(self):
        """--format yaml emits YAML output."""
        result = runner.invoke(
            app, ["map", CRM_EXPORT_CSV, CANONICAL_CUSTOMERS_CSV, "--format", "yaml"]
        )
        assert result.exit_code == 0, f"Output:\n{result.output}"
        assert "mappings" in result.output

    def test_map_saves_config(self, tmp_path):
        """--output saves a YAML config file."""
        out_yaml = str(tmp_path / "saved.yaml")
        result = runner.invoke(
            app,
            ["map", CRM_EXPORT_CSV, CANONICAL_CUSTOMERS_CSV, "-o", out_yaml],
        )
        assert result.exit_code == 0, f"Output:\n{result.output}"
        assert Path(out_yaml).exists()
        with open(out_yaml) as fh:
            data = yaml.safe_load(fh)
        assert "mappings" in data

    def test_map_with_min_confidence(self):
        """--min-confidence is accepted and does not crash."""
        result = runner.invoke(
            app,
            ["map", CRM_EXPORT_CSV, CANONICAL_CUSTOMERS_CSV, "--min-confidence", "0.5"],
        )
        assert result.exit_code == 0, f"Output:\n{result.output}"


class TestInspectCommand:
    def test_inspect_exit_code_zero(self):
        """inspect command exits with code 0."""
        result = runner.invoke(app, ["inspect", CRM_EXPORT_CSV])
        assert result.exit_code == 0, f"Output:\n{result.output}"

    def test_inspect_shows_fields(self):
        """inspect shows the field names from the source file."""
        result = runner.invoke(app, ["inspect", CRM_EXPORT_CSV])
        assert result.exit_code == 0
        assert "fname" in result.output

    def test_inspect_shows_stats(self):
        """inspect shows type information."""
        result = runner.invoke(app, ["inspect", CRM_EXPORT_CSV])
        assert result.exit_code == 0
        # Should show a type header or field types
        assert "string" in result.output or "FIELD" in result.output


class TestApplyCommand:
    def _make_mapping_yaml(self, tmp_path) -> str:
        """Create a simple mapping YAML for crm_export.csv -> canonical_customers.csv."""
        data = {
            "version": "1",
            "mappings": [
                {"source": "fname", "target": "first_name", "confidence": 0.95},
                {"source": "lname", "target": "last_name", "confidence": 0.92},
                {"source": "email_addr", "target": "email", "confidence": 0.88},
            ],
            "unmapped_source": ["tel", "zipcode"],
            "unmapped_target": ["phone", "zip_code"],
        }
        path = tmp_path / "mapping.yaml"
        with open(path, "w") as fh:
            yaml.dump(data, fh, default_flow_style=False, sort_keys=False)
        return str(path)

    def test_apply_exit_zero(self, tmp_path):
        """apply command exits with code 0 and writes output CSV."""
        mapping_yaml = self._make_mapping_yaml(tmp_path)
        out_csv = str(tmp_path / "output.csv")

        result = runner.invoke(
            app,
            ["apply", CRM_EXPORT_CSV, "--config", mapping_yaml, "-o", out_csv],
        )
        assert result.exit_code == 0, f"Output:\n{result.output}\nException:\n{result.exception}"
        assert Path(out_csv).exists()

    def test_apply_renames_columns(self, tmp_path):
        """apply renames columns according to the mapping."""
        import polars as pl

        mapping_yaml = self._make_mapping_yaml(tmp_path)
        out_csv = str(tmp_path / "output.csv")

        runner.invoke(
            app,
            ["apply", CRM_EXPORT_CSV, "--config", mapping_yaml, "-o", out_csv],
        )
        df = pl.read_csv(out_csv)
        assert "first_name" in df.columns
        assert "last_name" in df.columns
        assert "email" in df.columns


class TestValidateCommand:
    def _make_minimal_mapping_yaml(self, tmp_path) -> str:
        """Create a mapping with only fname->first_name."""
        data = {
            "version": "1",
            "mappings": [
                {"source": "fname", "target": "first_name", "confidence": 0.95},
            ],
            "unmapped_source": [],
            "unmapped_target": [],
        }
        path = tmp_path / "minimal_mapping.yaml"
        with open(path, "w") as fh:
            yaml.dump(data, fh, default_flow_style=False, sort_keys=False)
        return str(path)

    def test_validate_passes(self, tmp_path):
        """validate exits 0 when all mapped source columns exist."""
        mapping_yaml = self._make_minimal_mapping_yaml(tmp_path)
        result = runner.invoke(
            app,
            ["validate", CRM_EXPORT_CSV, "--config", mapping_yaml],
        )
        assert result.exit_code == 0, f"Output:\n{result.output}"

    def test_validate_strict_fails_when_required_unmapped(self, tmp_path):
        """--strict exits code 1 when required fields are not in the mappings."""
        mapping_yaml = self._make_minimal_mapping_yaml(tmp_path)
        result = runner.invoke(
            app,
            [
                "validate",
                CRM_EXPORT_CSV,
                "--config",
                mapping_yaml,
                "--strict",
                "--required",
                "email,phone",
            ],
        )
        assert result.exit_code == 1, (
            f"Expected exit code 1 but got {result.exit_code}.\nOutput:\n{result.output}"
        )

    def test_validate_shows_missing_columns(self, tmp_path):
        """validate reports missing source columns in the output."""
        data = {
            "version": "1",
            "mappings": [
                {"source": "nonexistent_col", "target": "first_name", "confidence": 0.95},
            ],
            "unmapped_source": [],
            "unmapped_target": [],
        }
        path = tmp_path / "bad_mapping.yaml"
        with open(path, "w") as fh:
            yaml.dump(data, fh, default_flow_style=False, sort_keys=False)

        result = runner.invoke(
            app,
            ["validate", CRM_EXPORT_CSV, "--config", str(path)],
        )
        assert result.exit_code == 0
        assert "nonexistent_col" in result.output or "Missing" in result.output
