"""MCP server smoke tests for goldenanalysis.

The tool HANDLERS are plain callables (no `mcp` dependency), so they run in the
default matrix lane (which installs goldenanalysis without the [mcp] extra). The
`TOOLS` list + `create_server` need `mcp`, so those are gated on `HAS_MCP`.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from goldenanalysis.mcp import server


def test_handlers_cover_the_tool_set() -> None:
    assert set(server.HANDLERS) == {"list_analyzers", "analyze_frame", "get_trend", "detect_regressions"}


def test_list_analyzers_handler() -> None:
    out = server.HANDLERS["list_analyzers"]({})
    assert "frame.summary" in out["analyzers"]
    assert "match.rates" in out["analyzers"]


def test_analyze_frame_handler(tmp_path: Path) -> None:
    csv = tmp_path / "customers.csv"
    csv.write_text("a,b\n1,x\n1,x\n2,y\n", encoding="utf-8")
    out = server.HANDLERS["analyze_frame"]({"path": str(csv)})
    keys = {m["key"] for m in out["metrics"]}
    assert "frame.row_count" in keys
    assert out["source"]["dataset"] == "customers"


def test_analyze_frame_rejects_unknown_suffix(tmp_path: Path) -> None:
    bad = tmp_path / "data.txt"
    bad.write_text("nope", encoding="utf-8")
    out = server.HANDLERS["analyze_frame"]({"path": str(bad)})
    assert "error" in out


def _seed_history(path: Path):
    from goldenanalysis.history import ReportHistory
    from goldenanalysis.models import AnalysisReport, Metric

    def _rep(run_id: str, val: float) -> AnalysisReport:
        return AnalysisReport(
            run_id=run_id,
            generated_at=datetime(2026, 6, 8, tzinfo=UTC),
            source={"dataset": "customers"},
            metrics=[Metric(key="cluster.singleton_ratio", value=val, unit="ratio", direction="neutral")],
        )

    hist = ReportHistory(backend="jsonl", path=path)
    for i in range(7):
        hist.append(_rep(f"r{i}", 0.58))
    hist.append(_rep("r7", 0.71))


def test_trend_handler(tmp_path: Path) -> None:
    path = tmp_path / "a.jsonl"
    _seed_history(path)
    series = server.HANDLERS["get_trend"](
        {"history": str(path), "metric": "cluster.singleton_ratio", "dataset": "customers"}
    )
    assert series["metric_key"] == "cluster.singleton_ratio"
    assert series["points"][-1] == ["r7", 0.71]


def test_detect_regressions_handler(tmp_path: Path) -> None:
    path = tmp_path / "a.jsonl"
    _seed_history(path)
    out = server.HANDLERS["detect_regressions"](
        {"history": str(path), "dataset": "customers", "policy": {"default_pct": 10}}
    )
    flagged = {r["metric"] for r in out["flagged"]}
    assert "cluster.singleton_ratio" in flagged  # +22.4% clears the 10% gate


@pytest.mark.skipif(not server.HAS_MCP, reason="mcp extra not installed")
def test_tools_and_server_build() -> None:
    names = {t.name for t in server.TOOLS}
    assert names == {"list_analyzers", "analyze_frame", "get_trend", "detect_regressions"}
    assert server.create_server() is not None
