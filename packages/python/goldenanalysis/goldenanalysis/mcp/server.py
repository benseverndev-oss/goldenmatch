"""MCP server with read-only GoldenAnalysis tools.

Mirrors ``goldenpipe/mcp/server.py``: module-level ``TOOLS`` (list of
``mcp.types.Tool``) + ``HANDLERS`` (tool name -> callable taking the args dict),
which ``goldensuite-mcp`` imports to surface these transitively. Every tool
lazy-imports the heavy bits (polars, ``ReportHistory``) so importing this module
stays light -- the aggregator + smoke tests only touch ``TOOLS`` / ``HANDLERS``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Prompt, Resource, TextContent, Tool

    HAS_MCP = True
except ImportError:
    HAS_MCP = False


def _open_history(history: str):
    from goldenanalysis.history import ReportHistory

    p = Path(history)
    backend = "sqlite" if p.suffix.lower() in (".db", ".sqlite") else "jsonl"
    return ReportHistory(backend=backend, path=p)


def list_analyzers_tool() -> dict[str, Any]:
    """List the discoverable analyzers."""
    from goldenanalysis.registry import available_analyzers

    return {"analyzers": available_analyzers()}


def analyze_frame_tool(path: str, analyzers: str | None = None, output_format: str = "json") -> dict[str, Any]:
    """Analyze a .parquet/.csv frame (or re-render a saved .json AnalysisReport)."""
    from goldenanalysis import analyze
    from goldenanalysis.models import AnalysisReport

    p = Path(path)
    suffix = p.suffix.lower()
    if suffix == ".json":
        rep = AnalysisReport.from_json(p.read_text(encoding="utf-8"))
    else:
        import polars as pl

        if suffix == ".parquet":
            df = pl.read_parquet(p)
        elif suffix == ".csv":
            df = pl.read_csv(p)
        else:
            return {"error": f"unsupported input type: {p.suffix!r} (want .parquet/.csv/.json)"}
        names = (
            None if not analyzers or analyzers == "all" else [a.strip() for a in analyzers.split(",") if a.strip()]
        )
        rep = analyze(df, analyzers=names, dataset=p.stem)

    if output_format == "markdown":
        return {"markdown": rep.to_markdown()}
    return json.loads(rep.to_json())


def get_trend_tool(history: str, metric: str, dataset: str, last: int = 30) -> dict[str, Any]:
    """Trend a metric over a ReportHistory (.jsonl/.db)."""
    series = _open_history(history).trend(metric, dataset, last_n=last)
    return {
        "metric_key": series.metric_key,
        "dataset": series.dataset,
        "points": [[run_id, value] for run_id, value in series.points],
    }


def detect_regressions_tool(
    history: str,
    dataset: str,
    baseline: str = "rolling_median",
    window: int = 7,
    policy: dict | None = None,
) -> dict[str, Any]:
    """Detect metric regressions vs a baseline over a ReportHistory."""
    from goldenanalysis.models import RegressionPolicy

    pol = (
        RegressionPolicy(
            default_pct=policy.get("default_pct", 10.0), per_metric=policy.get("per_metric", {})
        )
        if policy
        else RegressionPolicy()
    )
    flagged = _open_history(history).detect_regressions(dataset, baseline=baseline, window=window, policy=pol)
    return {
        "flagged": [
            {
                "metric": r.metric,
                "baseline": r.baseline,
                "current": r.current,
                "delta_pct": r.delta_pct,
                "direction": r.direction,
            }
            for r in flagged
        ]
    }


def _build_tools() -> list:
    """Build the Tool list. Lazy so import doesn't fail without mcp installed."""
    return [
        Tool(
            name="list_analyzers",
            description="List the discoverable GoldenAnalysis analyzers",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="analyze_frame",
            description="Analyze a .parquet/.csv frame (or re-render a .json AnalysisReport) into a metrics report",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "A .parquet/.csv frame or a .json AnalysisReport"},
                    "analyzers": {"type": "string", "description": "Comma-separated analyzer names, or 'all'"},
                    "output_format": {"type": "string", "enum": ["json", "markdown"]},
                },
                "required": ["path"],
            },
        ),
        Tool(
            name="get_trend",
            description="Trend a metric over a run history (.jsonl/.db ReportHistory)",
            inputSchema={
                "type": "object",
                "properties": {
                    "history": {"type": "string"},
                    "metric": {"type": "string"},
                    "dataset": {"type": "string"},
                    "last": {"type": "integer"},
                },
                "required": ["history", "metric", "dataset"],
            },
        ),
        Tool(
            name="detect_regressions",
            description="Detect metric regressions vs a baseline over a run history",
            inputSchema={
                "type": "object",
                "properties": {
                    "history": {"type": "string"},
                    "dataset": {"type": "string"},
                    "baseline": {"type": "string"},
                    "window": {"type": "integer"},
                    "policy": {"type": "object"},
                },
                "required": ["history", "dataset"],
            },
        ),
    ]


# Module-level surfaces for goldensuite-mcp (the in-process aggregator).
# TOOLS is a list of mcp.types.Tool; HANDLERS maps tool name -> callable taking the
# arguments dict and returning a JSON-serializable result.
TOOLS = _build_tools() if HAS_MCP else []
HANDLERS = {
    "list_analyzers": lambda args: list_analyzers_tool(),
    "analyze_frame": lambda args: analyze_frame_tool(**args),
    "get_trend": lambda args: get_trend_tool(**args),
    "detect_regressions": lambda args: detect_regressions_tool(**args),
}


def create_server() -> Server:
    """Create and configure the MCP server instance."""
    if not HAS_MCP:
        raise ImportError("MCP not installed. Run: pip install goldenanalysis[mcp]")

    server = Server("GoldenAnalysis")

    @server.list_tools()
    async def handle_list_tools():
        return TOOLS

    @server.list_resources()
    async def handle_list_resources() -> list[Resource]:
        return []

    @server.list_prompts()
    async def handle_list_prompts() -> list[Prompt]:
        return []

    @server.call_tool()
    async def handle_call_tool(name: str, arguments: dict):
        handler = HANDLERS.get(name)
        if handler is None:
            result = {"error": f"Unknown tool: {name}"}
        else:
            try:
                result = handler(arguments)
            except Exception as exc:  # noqa: BLE001 — surface a structured error, never crash the call
                result = {"error": str(exc)}
        return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

    return server


def run_server() -> None:
    """Start the MCP server over stdio transport."""
    import asyncio

    server = create_server()

    async def main():
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    asyncio.run(main())


def run_server_http(host: str = "0.0.0.0", port: int = 8300) -> None:
    """Start the MCP server over Streamable HTTP transport (A2A port 8300)."""
    import contextlib
    from collections.abc import AsyncIterator

    import uvicorn
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Mount, Route

    server = create_server()
    session_manager = StreamableHTTPSessionManager(app=server, json_response=False, stateless=False)

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        async with session_manager.run():
            yield

    async def server_card(request):
        return JSONResponse(
            {
                "name": "GoldenAnalysis",
                "description": "Read-only cross-cutting analysis, metrics, and reporting across the Golden Suite. 4 MCP tools: list analyzers, analyze a frame, trend a metric over a run history, and detect regressions vs a baseline.",
                "homepage": "https://github.com/benseverndev-oss/goldenmatch/tree/main/packages/python/goldenanalysis",
                "iconUrl": "https://avatars.githubusercontent.com/u/192581748",
            }
        )

    starlette_app = Starlette(
        debug=False,
        routes=[
            Route("/.well-known/mcp/server-card.json", server_card),
            Mount("/mcp", app=session_manager.handle_request),
        ],
        lifespan=lifespan,
    )

    uvicorn.run(starlette_app, host=host, port=port)
