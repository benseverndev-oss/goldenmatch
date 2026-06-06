"""Wave 3.2: CLI consistency fixes.

- match runs zero-config (auto-detects) when --config is omitted, like dedupe.
- dedupe --backend help lists all valid backends (bucket, chunked).
"""
from __future__ import annotations

from goldenmatch.cli.main import app
from typer.testing import CliRunner

runner = CliRunner()


class TestDedupeBackendHelp:
    def test_backend_help_lists_all_backends(self):
        # Introspect the registered param help, not the Rich-rendered table.
        from typer.main import get_command

        dedupe = get_command(app).commands["dedupe"]
        backend = next(p for p in dedupe.params if "--backend" in p.opts)
        for value in ("bucket", "chunked", "ray", "duckdb"):
            assert value in backend.help


class TestMatchZeroConfig:
    def test_config_is_optional(self):
        from typer.main import get_command

        match = get_command(app).commands["match"]
        cfg = next(p for p in match.params if "--config" in p.opts)
        assert cfg.required is False

    def test_match_runs_without_config(self, tmp_path):
        target = tmp_path / "target.csv"
        ref = tmp_path / "ref.csv"
        # Two columns only -> auto-config won't enable the 3+-field reranker
        # (which would need an offline-unfriendly HF download in CI).
        target.write_text("name,email\nAlice,a@x.com\n")
        ref.write_text("name,email\nAlicia,a@x.com\nBob,b@y.com\n")

        result = runner.invoke(
            app, ["match", str(target), "--against", str(ref), "--quiet"]
        )
        assert result.exit_code == 0, result.stdout
