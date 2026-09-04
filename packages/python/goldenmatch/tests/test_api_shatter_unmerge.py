"""Wave 3.3: REST matching server can now edit clusters (shatter / unmerge).

The live server could read clusters but not mutate them; these mirror the MCP
shatter_cluster tool + engine.unmerge_record/unmerge_cluster.
"""
from __future__ import annotations

from goldenmatch.api.server import MatchServer
from goldenmatch.config.schemas import (
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.tui.engine import EngineResult, EngineStats


def _cfg() -> GoldenMatchConfig:
    return GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="email_exact",
                type="exact",
                fields=[MatchkeyField(field="email")],
            )
        ]
    )


def _stats(total_clusters: int) -> EngineStats:
    return EngineStats(
        total_records=3,
        total_clusters=total_clusters,
        singleton_count=3 - 2 * total_clusters,
        match_rate=0.0,
        cluster_sizes=[],
        avg_cluster_size=0.0,
        max_cluster_size=0,
        oversized_count=0,
    )


def _result(clusters: dict, total_clusters: int) -> EngineResult:
    return EngineResult(
        clusters=clusters,
        golden=None,
        unique=None,
        dupes=None,
        quarantine=None,
        matched=None,
        unmatched=None,
        scored_pairs=[],
        stats=_stats(total_clusters),
    )


class _StubEngine:
    def __init__(self, after: EngineResult):
        self._after = after

    def unmerge_cluster(self, cluster_id):
        return self._after

    def unmerge_record(self, record_id, threshold=0.0):
        return self._after


_BEFORE = {0: {"members": [0, 1, 2], "size": 3, "oversized": False, "confidence": 0.9}}
_AFTER_SHATTERED = _result(
    {
        1: {"members": [0], "size": 1, "oversized": False, "confidence": 1.0},
        2: {"members": [1], "size": 1, "oversized": False, "confidence": 1.0},
        3: {"members": [2], "size": 1, "oversized": False, "confidence": 1.0},
    },
    total_clusters=0,
)


class TestShatter:
    def test_shatter_returns_summary_and_updates_result(self):
        srv = MatchServer(_StubEngine(_AFTER_SHATTERED), _cfg())
        srv.result = _result(_BEFORE, total_clusters=1)
        out = srv.shatter_cluster(0)
        assert out["status"] == "shattered"
        assert out["records_freed"] == 3
        assert out["total_clusters"] == 0
        assert srv.result is _AFTER_SHATTERED

    def test_shatter_missing_cluster_errors(self):
        srv = MatchServer(_StubEngine(_AFTER_SHATTERED), _cfg())
        srv.result = _result(_BEFORE, total_clusters=1)
        out = srv.shatter_cluster(99)
        assert "error" in out

    def test_shatter_uninitialized_errors(self):
        srv = MatchServer(_StubEngine(_AFTER_SHATTERED), _cfg())
        assert "error" in srv.shatter_cluster(0)


class TestShatterMirrorsMcpTool:
    """api/server.py MatchServer.shatter_cluster's docstring claims it "mirrors
    the MCP shatter_cluster tool" (goldenmatch.mcp.server._tool_shatter_cluster).
    Both wrap engine.unmerge_cluster with the same pre-check / summary shape --
    assert that shape stays identical given equivalent state.
    """

    def test_shatter_output_matches_mcp_tool_shape(self, monkeypatch):
        import goldenmatch.mcp.server as mcp_server

        monkeypatch.setattr(mcp_server, "_engine", _StubEngine(_AFTER_SHATTERED), raising=False)
        monkeypatch.setattr(mcp_server, "_result", _result(_BEFORE, total_clusters=1), raising=False)
        mcp_out = mcp_server._tool_shatter_cluster(0)

        srv = MatchServer(_StubEngine(_AFTER_SHATTERED), _cfg())
        srv.result = _result(_BEFORE, total_clusters=1)
        api_out = srv.shatter_cluster(0)

        assert api_out == mcp_out

    def test_shatter_missing_cluster_error_matches_mcp_tool(self, monkeypatch):
        import goldenmatch.mcp.server as mcp_server

        monkeypatch.setattr(mcp_server, "_engine", _StubEngine(_AFTER_SHATTERED), raising=False)
        monkeypatch.setattr(mcp_server, "_result", _result(_BEFORE, total_clusters=1), raising=False)
        mcp_out = mcp_server._tool_shatter_cluster(99)

        srv = MatchServer(_StubEngine(_AFTER_SHATTERED), _cfg())
        srv.result = _result(_BEFORE, total_clusters=1)
        api_out = srv.shatter_cluster(99)

        assert api_out == mcp_out


class TestUnmerge:
    def test_unmerge_record_updates_result(self):
        after = _result(
            {
                0: {"members": [0, 1], "size": 2, "oversized": False, "confidence": 0.9},
                4: {"members": [2], "size": 1, "oversized": False, "confidence": 1.0},
            },
            total_clusters=1,
        )
        srv = MatchServer(_StubEngine(after), _cfg())
        srv.result = _result(_BEFORE, total_clusters=1)
        out = srv.unmerge_record_op(2)
        assert out["status"] == "unmerged"
        assert out["record_id"] == 2
        assert srv.result is after

    def test_unmerge_uninitialized_errors(self):
        srv = MatchServer(_StubEngine(_AFTER_SHATTERED), _cfg())
        assert "error" in srv.unmerge_record_op(0)
