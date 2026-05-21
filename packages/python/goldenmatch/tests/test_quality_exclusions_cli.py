"""Tests for the --exclusions CLI flag (#404, plan step 9)."""

from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.cli.main import app
from typer.testing import CliRunner


@pytest.fixture
def poisoned_csv(tmp_path):
    """Write a small CSV with two poisoned columns + clean identity cols."""
    df = pl.DataFrame({
        "first_name": [f"name_{i}" for i in range(50)],
        "last_name": [f"smith_{i % 5}" for i in range(50)],
        "external_id": [f"ext_{i:08d}" for i in range(50)],     # foreign_system_id
        "record_hash": [f"{i:032x}" for i in range(50)],         # system_hash
    })
    path = tmp_path / "poisoned.csv"
    df.write_csv(path)
    return path


def test_profile_exclusions_flag_lists_auto_excluded_columns(poisoned_csv):
    """`goldenmatch profile FILE --exclusions` prints the exclusion list
    so users can see what auto-config will skip without running a full
    sync."""
    runner = CliRunner()
    result = runner.invoke(app, ["profile", str(poisoned_csv), "--exclusions"])
    assert result.exit_code == 0, result.output

    assert "Auto-config exclusions" in result.output
    assert "external_id" in result.output
    assert "foreign_system_id" in result.output
    assert "record_hash" in result.output
    assert "system_hash" in result.output


def test_profile_without_exclusions_flag_omits_section(poisoned_csv):
    """Default `goldenmatch profile FILE` does NOT print the exclusion
    section -- only when --exclusions is passed."""
    runner = CliRunner()
    result = runner.invoke(app, ["profile", str(poisoned_csv)])
    assert result.exit_code == 0, result.output
    assert "Auto-config exclusions" not in result.output


def test_profile_exclusions_flag_shows_friendly_message_for_clean_data(tmp_path):
    """Clean dataset with no poisoned columns -> 'No columns excluded'
    message instead of an empty exclusion list."""
    df = pl.DataFrame({
        "first_name": ["Alice", "Bob", "Carol"] * 10,
        "last_name": ["Smith", "Jones", "Doe"] * 10,
        "city": ["NYC", "LA", "SF"] * 10,
    })
    path = tmp_path / "clean.csv"
    df.write_csv(path)

    runner = CliRunner()
    result = runner.invoke(app, ["profile", str(path), "--exclusions"])
    assert result.exit_code == 0, result.output
    assert "No columns excluded" in result.output


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
