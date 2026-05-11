"""Regression tests: the [web] extra must be optional.

A user who installs plain `goldenmatch` (no `[web]`) should still be able to
run every CLI command except `serve-ui`. Module-load imports of fastapi /
uvicorn / goldenmatch.web from anywhere on the CLI hot path would break that.
"""
from __future__ import annotations

import builtins
import importlib
import sys
import types

import pytest
from typer.testing import CliRunner

WEB_MODULES = {
    "fastapi",
    "fastapi.staticfiles",
    "fastapi.testclient",
    "uvicorn",
    "goldenmatch.web",
    "goldenmatch.web.app",
    "goldenmatch.web.state",
    "goldenmatch.web.rules",
    "goldenmatch.web.runs",
    "goldenmatch.web.routers",
    "goldenmatch.web.routers.project",
    "goldenmatch.web.routers.rules",
    "goldenmatch.web.routers.runs",
}


@pytest.fixture
def no_web_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate a fresh interpreter with no fastapi/uvicorn/goldenmatch.web."""
    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> types.ModuleType:
        if name in WEB_MODULES or name.startswith("fastapi") or name == "uvicorn":
            raise ImportError(f"No module named {name!r} (simulated for test)")
        return real_import(name, *args, **kwargs)

    for mod in list(sys.modules):
        if mod in WEB_MODULES or mod.startswith("fastapi") or mod == "uvicorn":
            monkeypatch.delitem(sys.modules, mod, raising=False)
    # Force re-import of the CLI modules so the fake_import path is taken.
    for mod in ("goldenmatch.cli.serve_ui", "goldenmatch.cli.main"):
        monkeypatch.delitem(sys.modules, mod, raising=False)
    monkeypatch.setattr(builtins, "__import__", fake_import)


def test_cli_main_imports_without_web_extra(no_web_extra: None) -> None:
    """Importing the CLI entry point must succeed even without fastapi."""
    main = importlib.import_module("goldenmatch.cli.main")
    assert hasattr(main, "app"), "goldenmatch.cli.main.app should exist"


def test_serve_ui_command_reports_missing_extra(no_web_extra: None) -> None:
    """`goldenmatch serve-ui` must fail with a clear message, not an ImportError."""
    main = importlib.import_module("goldenmatch.cli.main")
    runner = CliRunner()
    result = runner.invoke(main.app, ["serve-ui", "--help"])
    # --help short-circuits before the dep check, so this should still succeed.
    assert result.exit_code == 0

    # Actually invoking the command without --help should surface the
    # BadParameter wrapping our installation hint.
    result = runner.invoke(main.app, ["serve-ui"])
    assert result.exit_code != 0
    assert "goldenmatch[web]" in (result.stdout + result.output)
