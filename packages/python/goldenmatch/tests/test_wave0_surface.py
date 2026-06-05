"""Wave 0 surface-gap fixes: CLI bugs + HTTP-server fail-closed auth.

Covers:
- 0.2 dedupe --preview / --merge-preview flag collision
- 0.3 review command is registered and runnable
- 0.4 unmerge actually re-clusters and writes output
- 0.1 MCP HTTP server fail-closed bind rule
- 0.5 A2A server fail-closed bind rule
"""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.cli.main import app
from typer.testing import CliRunner

runner = CliRunner()


class TestPreviewFlagCollision:
    """0.2 -- the two flags must be distinct, not both claim --preview."""

    def test_both_preview_flags_in_help(self):
        result = runner.invoke(app, ["dedupe", "--help"])
        assert result.exit_code == 0
        assert "--preview" in result.stdout
        assert "--merge-preview" in result.stdout


class TestReviewCommandRegistered:
    """0.3 -- review was documented but never registered."""

    def test_review_help(self):
        result = runner.invoke(app, ["review", "--help"])
        assert result.exit_code == 0
        assert "review" in result.stdout.lower()

    def test_review_quickstart_flag_parses(self):
        # The README quickstart is `goldenmatch review --config goldenmatch.yml`.
        # With a missing config it must fail cleanly, not "No such command".
        result = runner.invoke(app, ["review", "--config", "does-not-exist.yml"])
        assert result.exit_code != 0
        assert "No such command" not in result.stdout

    def test_review_surfaces_pipeline_stale_queue(self, tmp_path):
        # The pipeline enqueues stale corrections to a sibling review_queue.db
        # under job "memory_stale"; `review` (no files) must surface them.
        from goldenmatch.core.review_queue import ReviewQueue

        queue_db = tmp_path / "review_queue.db"
        rq = ReviewQueue(backend="sqlite", path=str(queue_db))
        rq.add("memory_stale", 7, 9, 0.82, "correction stale: re-decide")
        rq.close()

        cfg = tmp_path / "gm.yml"
        cfg.write_text(
            "matchkeys:\n"
            "  - name: email_exact\n"
            "    fields:\n"
            "      - column: email\n"
            "    comparison: exact\n"
        )

        result = runner.invoke(
            app,
            [
                "review",
                "--config", str(cfg),
                "--queue-path", str(queue_db),
                "--memory-path", str(tmp_path / "memory.db"),
            ],
            input="y\n",
        )
        assert result.exit_code == 0, result.stdout
        assert "Approved 1" in result.stdout


class TestUnmergeRoundTrip:
    """0.4 -- unmerge must actually call the core re-cluster, not just log."""

    def _write_clusters(self, path) -> None:
        # One cluster (id 0) with three members; record 2 is the odd one out.
        pl.DataFrame(
            {
                "__row_id__": [0, 1, 2],
                "__cluster_id__": [0, 0, 0],
                "name": ["alice", "alice", "bob"],
            }
        ).write_csv(path)

    def test_unmerge_record_reassigns_and_writes(self, tmp_path):
        clusters = tmp_path / "clusters.csv"
        self._write_clusters(clusters)

        result = runner.invoke(app, ["unmerge", "2", "--clusters", str(clusters)])
        assert result.exit_code == 0, result.stdout

        out = tmp_path / "clusters.unmerged.csv"
        assert out.exists()
        df = pl.read_csv(out)
        cid = dict(zip(df["__row_id__"], df["__cluster_id__"]))
        # Record 2 must no longer share a cluster with 0 and 1.
        assert cid[2] != cid[0]

    def test_unmerge_requires_clusters(self, tmp_path):
        result = runner.invoke(app, ["unmerge", "2"])
        assert result.exit_code == 2
        assert "clusters" in result.stdout.lower()

    def test_unmerge_shatter_splits_all(self, tmp_path):
        clusters = tmp_path / "clusters.csv"
        self._write_clusters(clusters)
        out = tmp_path / "shattered.csv"
        result = runner.invoke(
            app, ["unmerge", "0", "--clusters", str(clusters), "--shatter", "-o", str(out)]
        )
        assert result.exit_code == 0, result.stdout
        df = pl.read_csv(out)
        # Every former member is now in its own cluster.
        assert df["__cluster_id__"].n_unique() == 3


class TestMcpFailClosed:
    """0.1 -- never start an unauthenticated MCP HTTP server on a public host."""

    def test_public_host_without_token_raises(self, monkeypatch):
        from goldenmatch.mcp.server import resolve_http_auth_token

        monkeypatch.delenv("GOLDENMATCH_MCP_TOKEN", raising=False)
        with pytest.raises(RuntimeError, match="unauthenticated"):
            resolve_http_auth_token("0.0.0.0")

    def test_loopback_without_token_allowed(self, monkeypatch):
        from goldenmatch.mcp.server import resolve_http_auth_token

        monkeypatch.delenv("GOLDENMATCH_MCP_TOKEN", raising=False)
        assert resolve_http_auth_token("127.0.0.1") is None

    def test_public_host_with_token_allowed(self, monkeypatch):
        from goldenmatch.mcp.server import resolve_http_auth_token

        monkeypatch.setenv("GOLDENMATCH_MCP_TOKEN", "secret")
        assert resolve_http_auth_token("0.0.0.0") == "secret"


class TestA2aFailClosed:
    """0.5 -- same posture for the A2A agent server."""

    def test_public_host_without_token_raises(self, monkeypatch):
        pytest.importorskip("aiohttp")
        from goldenmatch.a2a.server import create_app

        monkeypatch.delenv("GOLDENMATCH_AGENT_TOKEN", raising=False)
        with pytest.raises(RuntimeError, match="unauthenticated"):
            create_app(host="0.0.0.0")

    def test_loopback_without_token_allowed(self, monkeypatch):
        pytest.importorskip("aiohttp")
        from goldenmatch.a2a.server import create_app

        monkeypatch.delenv("GOLDENMATCH_AGENT_TOKEN", raising=False)
        app_obj = create_app(host="127.0.0.1")
        assert app_obj is not None

    def test_public_host_with_token_allowed(self, monkeypatch):
        pytest.importorskip("aiohttp")
        from goldenmatch.a2a.server import create_app

        monkeypatch.setenv("GOLDENMATCH_AGENT_TOKEN", "secret")
        app_obj = create_app(host="0.0.0.0")
        assert app_obj is not None
