"""Tests for `goldenmatch llm serve-local` (D1 Path A).

The server launch is monkeypatched out; only the resolve/guard logic and the
env-var hint output are exercised.
"""

from __future__ import annotations

from goldenmatch.cli import llm as llm_mod
from goldenmatch.cli.main import app
from goldenmatch.core import _llm_loader
from typer.testing import CliRunner

runner = CliRunner()


def test_unresolvable_model_exits_with_hint(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_LOCAL_LLM_PATH", raising=False)
    # The command does a function-local `from _llm_loader import resolve_model_path`,
    # so patch the loader module's attribute.
    monkeypatch.setattr(_llm_loader, "resolve_model_path", lambda *a, **k: None)
    result = runner.invoke(app, ["llm", "serve-local"])
    assert result.exit_code == 1
    assert "No local model could be resolved" in result.output


def test_launches_and_prints_base_url_hint(monkeypatch, tmp_path):
    model = tmp_path / "m.gguf"
    model.write_bytes(b"gguf")
    launched: dict = {}
    monkeypatch.setattr(llm_mod, "_llama_server_available", lambda: True)
    monkeypatch.setattr(
        llm_mod, "_launch_server",
        lambda path, host, port: launched.update(path=path, host=host, port=port),
    )
    result = runner.invoke(
        app,
        ["llm", "serve-local", "--model-path", str(model), "--port", "9099"],
    )
    assert result.exit_code == 0, result.output
    assert launched == {"path": str(model), "host": "127.0.0.1", "port": 9099}
    assert "GOLDENMATCH_LLM_BASE_URL=http://127.0.0.1:9099/v1" in result.output
