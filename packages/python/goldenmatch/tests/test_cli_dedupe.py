"""Tests for CLI dedupe command."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest
from goldenmatch.cli.dedupe import _parse_file_source
from goldenmatch.cli.main import app
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture
def cli_csv(tmp_path) -> Path:
    path = tmp_path / "data.csv"
    df = pl.DataFrame({
        "first_name": ["John", "john", "Jane", "JOHN", "Bob"],
        "last_name": ["Smith", "Smith", "Doe", "Smyth", "Jones"],
        "email": ["john@ex.com", "john@ex.com", "jane@t.com", "john@ex.com", "bob@t.com"],
    })
    df.write_csv(path)
    return path


@pytest.fixture
def simple_config(tmp_path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(textwrap.dedent("""\
        matchkeys:
          - name: exact_email
            type: exact
            fields:
              - field: email
                transforms: [lowercase, strip]
    """))
    return path


class TestParseFileSource:
    def test_plain_path(self):
        path, source = _parse_file_source("/data/file.csv")
        assert path == "/data/file.csv"
        assert source == "file"

    def test_path_with_source(self):
        path, source = _parse_file_source("/data/file.csv:my_source")
        assert path == "/data/file.csv"
        assert source == "my_source"

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows drive letter paths")
    def test_windows_drive_letter(self):
        path, source = _parse_file_source("C:\\data\\file.csv")
        assert path == "C:\\data\\file.csv"
        assert source == "file"

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows drive letter paths")
    def test_windows_with_source(self):
        path, source = _parse_file_source("C:\\data\\file.csv:my_source")
        # The last colon separates source, but C: at start is a drive
        # rfind finds last colon -> "C:\\data\\file.csv" : "my_source"
        assert path == "C:\\data\\file.csv"
        assert source == "my_source"


class TestDedupeCmd:
    def test_basic_dedupe(self, cli_csv, simple_config):
        result = runner.invoke(app, [
            "dedupe",
            str(cli_csv),
            "--config", str(simple_config),
            "--quiet",
        ])
        assert result.exit_code == 0

    def test_missing_config(self, cli_csv):
        result = runner.invoke(app, [
            "dedupe",
            str(cli_csv),
            "--config", "/nonexistent/config.yaml",
        ])
        assert result.exit_code == 1
        assert "Config error" in result.output

    def test_help(self):
        result = runner.invoke(app, ["dedupe", "--help"])
        assert result.exit_code == 0
        assert "dedupe" in result.output.lower()

    def test_output_report(self, cli_csv, simple_config):
        result = runner.invoke(app, [
            "dedupe",
            str(cli_csv),
            "--config", str(simple_config),
            "--output-report",
        ])
        assert result.exit_code == 0
        assert "Dedupe Report" in result.output

    def test_output_all(self, cli_csv, simple_config, tmp_path):
        result = runner.invoke(app, [
            "dedupe",
            str(cli_csv),
            "--config", str(simple_config),
            "--output-all",
            "--output-dir", str(tmp_path / "out"),
        ])
        assert result.exit_code == 0


class TestHealerSurface:
    """CLI surface for the config-suggestion (healer): --suggest / --heal flags
    plus the default-run one-line hint."""

    def _fake_result(self, suggestions=None, heal_trail=None):
        return SimpleNamespace(
            suggestions=suggestions or [],
            heal_trail=heal_trail,
        )

    def test_suggest_flag_accepted_and_renders(self, cli_csv, simple_config, monkeypatch):
        """--suggest is accepted (exit 0) and prints the surfaced suggestions."""
        sug = {
            "id": "thr-name", "kind": "threshold", "target": "name",
            "rationale": "lower threshold to 0.8", "verified": True, "patch": {},
        }
        captured = {}

        def fake_dedupe_df(df, **kwargs):
            captured.update(kwargs)
            return self._fake_result(suggestions=[sug])

        monkeypatch.setattr("goldenmatch._api.dedupe_df", fake_dedupe_df)

        result = runner.invoke(app, [
            "dedupe", str(cli_csv), "--config", str(simple_config), "--suggest",
        ])
        assert result.exit_code == 0, result.stderr
        assert captured.get("suggest") is True
        assert "threshold" in result.output
        assert "lower threshold to 0.8" in result.output

    def test_heal_flag_prints_trail(self, cli_csv, simple_config, monkeypatch):
        """--heal prints the applied trail and a healed-config note."""
        applied = {
            "id": "thr-name", "kind": "threshold", "target": "name",
            "rationale": "raise threshold to 0.9", "verified": True, "patch": {},
        }

        def fake_dedupe_df(df, **kwargs):
            assert kwargs.get("heal") is True
            return self._fake_result(suggestions=[applied], heal_trail=[applied])

        monkeypatch.setattr("goldenmatch._api.dedupe_df", fake_dedupe_df)

        result = runner.invoke(app, [
            "dedupe", str(cli_csv), "--config", str(simple_config), "--heal",
        ])
        assert result.exit_code == 0, result.stderr
        assert "healed" in result.output.lower()
        assert "raise threshold to 0.9" in result.output

    def test_default_run_prints_hint_when_suggestions_present(
        self, cli_csv, simple_config, monkeypatch
    ):
        """A default (zero-config) run prints the one-line hint to stderr when
        the healer surfaced candidates."""
        from goldenmatch.config.loader import load_config

        cfg = load_config(str(simple_config))

        monkeypatch.setenv("GOLDENMATCH_SUGGEST_ON_DEDUPE", "1")
        # Take the zero-config path cheaply: stub auto-config + the controller
        # capture + the dedupe run so we don't run a real pipeline.
        monkeypatch.setattr(
            "goldenmatch.core.autoconfig.auto_configure", lambda files: cfg
        )
        monkeypatch.setattr(
            "goldenmatch.cli._controller_render.capture_controller_state",
            lambda: (None, None),
        )
        monkeypatch.setattr(
            "goldenmatch.core.pipeline.run_dedupe",
            lambda **kwargs: {"clusters": {}},
        )
        sug = {
            "id": "thr-name", "kind": "threshold", "target": "name",
            "rationale": "lower threshold", "verified": False, "patch": {},
        }
        monkeypatch.setattr(
            "goldenmatch._api.dedupe_df",
            lambda df, **kwargs: self._fake_result(suggestions=[sug]),
        )

        result = runner.invoke(app, ["dedupe", str(cli_csv), "--no-tui"])
        assert result.exit_code == 0, result.stderr
        assert "suggestion(s) to improve this config" in result.stderr
        assert "--suggest" in result.stderr

    def test_default_run_no_hint_with_explicit_config(
        self, cli_csv, simple_config, monkeypatch
    ):
        """An explicit --config run surfaces nothing by default (no controller
        history) -- dedupe_df is never reached for the hint."""
        called = {"n": 0}

        def fake_dedupe_df(df, **kwargs):
            called["n"] += 1
            return self._fake_result(suggestions=[{"id": "x", "kind": "k",
                                                   "target": "t", "rationale": "r"}])

        monkeypatch.setattr("goldenmatch._api.dedupe_df", fake_dedupe_df)
        result = runner.invoke(app, [
            "dedupe", str(cli_csv), "--config", str(simple_config),
        ])
        assert result.exit_code == 0, result.stderr
        assert called["n"] == 0
        assert "suggestion(s) to improve this config" not in (result.stderr or "")


class TestMatchCmd:
    def test_help(self):
        result = runner.invoke(app, ["match", "--help"])
        assert result.exit_code == 0
        assert "match" in result.output.lower()
