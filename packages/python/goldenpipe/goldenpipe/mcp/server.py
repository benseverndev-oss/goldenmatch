"""MCP server with pipeline tools."""
from __future__ import annotations

import json
from typing import Any

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Prompt, Resource, TextContent, Tool
    HAS_MCP = True
except ImportError:
    HAS_MCP = False

from goldenpipe.engine.registry import StageRegistry
from goldenpipe.engine.resolver import Resolver, WiringError
from goldenpipe.models.config import PipelineConfig, StageSpec


def list_stages_tool() -> dict[str, Any]:
    """List all discovered stages."""
    reg = StageRegistry()
    reg.discover()
    return {
        name: {"produces": info.produces, "consumes": info.consumes}
        for name, info in reg.list_all().items()
    }


def validate_pipeline_tool(pipeline: str, stages: list[str]) -> dict[str, Any]:
    """Validate pipeline wiring."""
    try:
        specs = [StageSpec(use=s) for s in stages]
        config = PipelineConfig(pipeline=pipeline, stages=specs)
        reg = StageRegistry()
        reg.discover()
        plan = Resolver.resolve(config, reg)
        return {"valid": True, "stages": [s.name for s in plan.stages]}
    except (WiringError, KeyError) as e:
        return {"valid": False, "error": str(e)}


def run_pipeline_tool(
    source: str | None = None,
    config_path: str | None = None,
    *,
    records: list[dict] | None = None,
    csv_text: str | None = None,
    stages: list[str] | None = None,
    identity_opts: dict | None = None,
    preview_rows: int = 10,
) -> dict[str, Any]:
    """Run a pipeline and return the FULL result an LLM needs to orchestrate.

    Input (one of, in precedence order): ``records`` (list of row dicts),
    ``csv_text`` (raw CSV), or ``source`` (a file path on the server). Config:
    ``stages`` (an inline stage-name list, e.g. ``["goldencheck.scan",
    "goldenmatch.dedupe"]``) or ``config_path`` (a YAML on disk); omit both for
    zero-config auto. ``identity_opts`` forwards the Identity Graph knobs.

    Returns per-stage ``status``/``reasoning``/``timing``, ``skipped``,
    ``errors``, AND — the reason this exists — an ``output`` block with the
    deduped result: golden-record count + a ``preview`` (first ``preview_rows``
    rows), unique/duplicate counts, cluster count, and match stats. This closes
    the fire-and-forget gap: the caller can see what the pipeline produced and
    branch on it, not just that it ran.
    """
    import polars as pl

    from goldenpipe.pipeline import Pipeline

    # Config: inline stages > YAML path > zero-config auto. An explicit (even
    # empty) ``stages`` list wins over auto; ``None`` means zero-config.
    cfg = None
    if stages is not None:
        cfg = PipelineConfig(pipeline="inline", stages=[StageSpec(use=s) for s in stages])
    elif config_path:
        from goldenpipe.config.loader import load_config
        cfg = load_config(config_path)

    try:
        pipe = Pipeline(config=cfg, identity_opts=identity_opts)
        if records is not None:
            result = pipe.run(df=pl.DataFrame(records))
        elif csv_text is not None:
            import io
            result = pipe.run(df=pl.read_csv(io.StringIO(csv_text), ignore_errors=True))
        elif source:
            result = pipe.run(source=source)
        else:
            return {"error": (
                "provide one input: 'source' (file path), 'records' (list of "
                "row dicts), or 'csv_text' (raw CSV)."
            )}
    except Exception as exc:  # surface config/build errors as data, not a crash
        return {"error": str(exc)}

    return _result_to_dict(result, preview_rows)


def _is_frame(obj: Any) -> bool:
    """A Polars-DataFrame-shaped artifact (duck-typed to avoid a hard import)."""
    return obj is not None and hasattr(obj, "height") and hasattr(obj, "to_dicts")


def _summarize_output(artifacts: dict, preview_rows: int) -> dict[str, Any]:
    """Pull the deduped output out of the pipeline artifacts, JSON-safe.

    The dedupe stage casts every column to string before matching, so
    ``to_dicts()`` on golden/unique frames is JSON-serializable.
    """
    out: dict[str, Any] = {}
    golden = artifacts.get("golden")
    if _is_frame(golden):
        out["golden_records"] = golden.height
        if preview_rows:
            out["golden_preview"] = golden.head(preview_rows).to_dicts()
    unique = artifacts.get("unique")
    if _is_frame(unique):
        out["unique_records"] = unique.height
    dupes = artifacts.get("dupes")
    if _is_frame(dupes):
        out["duplicate_records"] = dupes.height
    stats = artifacts.get("match_stats")
    if isinstance(stats, dict):
        out["match_stats"] = stats
    clusters = artifacts.get("clusters")
    if clusters is not None:
        try:
            out["cluster_count"] = len(clusters)
        except TypeError:
            pass
    return out


def _result_to_dict(result: Any, preview_rows: int = 10) -> dict[str, Any]:
    """Serialize a ``PipeResult`` into the full JSON payload for MCP/A2A."""
    preview_rows = max(0, min(int(preview_rows), 100))  # bound the payload
    out: dict[str, Any] = {
        "status": result.status.value,
        "source": result.source,
        "input_rows": result.input_rows,
        "stages": {
            name: ({"status": r.status.value, "error": r.error}
                   if r.error else {"status": r.status.value})
            for name, r in result.stages.items()
        },
        "reasoning": dict(result.reasoning),
        "timing": {k: round(float(v), 4) for k, v in result.timing.items()},
        "skipped": list(result.skipped),
        "errors": list(result.errors),
    }
    output = _summarize_output(result.artifacts, preview_rows)
    if output:
        out["output"] = output
    return out


def explain_pipeline_tool(config_path: str) -> dict[str, Any]:
    """Explain what a pipeline config will do."""
    from goldenpipe.config.loader import load_config
    config = load_config(config_path)
    reg = StageRegistry()
    reg.discover()
    try:
        plan = Resolver.resolve(config, reg)
        return {
            "pipeline": config.pipeline,
            "stages": [
                {"name": s.name, "produces": s.stage.info.produces, "consumes": s.stage.info.consumes}
                for s in plan.stages
            ],
        }
    except Exception as e:
        return {"error": str(e)}


def _build_tools() -> list:
    """Build the Tool list. Lazy so import doesn't fail without mcp installed."""
    return [
        Tool(name="list_stages", description="List all discovered pipeline stages",
             inputSchema={"type": "object", "properties": {}}),
        Tool(name="validate_pipeline", description="Validate pipeline wiring",
             inputSchema={"type": "object", "properties": {
                 "pipeline": {"type": "string"},
                 "stages": {"type": "array", "items": {"type": "string"}},
             }, "required": ["pipeline", "stages"]}),
        Tool(name="run_pipeline",
             description=(
                 "Run a pipeline on a file OR inline data (records / csv_text) and "
                 "return per-stage status, reasoning and timing PLUS the deduped "
                 "output (golden-record count + preview, unique/duplicate counts, "
                 "cluster count, match stats). Give a `stages` list or `config_path` "
                 "to control the chain, or omit both for zero-config auto."
             ),
             inputSchema={"type": "object", "properties": {
                 "source": {"type": "string", "description": "Input file path on the server."},
                 "records": {"type": "array", "items": {"type": "object"},
                             "description": "Inline data as a list of row dicts."},
                 "csv_text": {"type": "string", "description": "Inline data as raw CSV text."},
                 "stages": {"type": "array", "items": {"type": "string"},
                            "description": "Inline stage chain, e.g. ['goldencheck.scan','goldenmatch.dedupe']."},
                 "config_path": {"type": "string", "description": "YAML pipeline config path on the server."},
                 "identity_opts": {"type": "object", "description": "Identity Graph options (zero-config mode only)."},
                 "preview_rows": {"type": "integer", "description": "Rows of golden output to preview (default 10, max 100)."},
             }}),
        Tool(name="explain_pipeline", description="Explain what a pipeline config does",
             inputSchema={"type": "object", "properties": {
                 "config_path": {"type": "string"},
             }, "required": ["config_path"]}),
    ]


# Module-level surfaces for goldensuite-mcp (the in-process aggregator).
# TOOLS is a list of mcp.types.Tool objects; HANDLERS maps tool name -> callable
# that takes the arguments dict and returns a JSON-serializable result.
TOOLS = _build_tools() if HAS_MCP else []
HANDLERS = {
    "list_stages": lambda args: list_stages_tool(),
    "validate_pipeline": lambda args: validate_pipeline_tool(**args),
    "run_pipeline": lambda args: run_pipeline_tool(**args),
    "explain_pipeline": lambda args: explain_pipeline_tool(**args),
}


def create_server() -> Server:
    """Create and configure the MCP server instance."""
    if not HAS_MCP:
        raise ImportError("MCP not installed. Run: pip install goldenpipe[mcp]")

    server = Server("GoldenPipe")

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
            except Exception as exc:
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


def run_server_http(host: str = "0.0.0.0", port: int = 8250) -> None:
    """Start the MCP server over Streamable HTTP transport."""
    import contextlib
    from collections.abc import AsyncIterator

    import uvicorn
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Mount, Route

    server = create_server()
    session_manager = StreamableHTTPSessionManager(
        app=server,
        json_response=False,
        stateless=False,
    )

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        async with session_manager.run():
            yield

    async def server_card(request):
        return JSONResponse({
            "name": "GoldenPipe",
            "description": "Orchestrator for the Golden Suite — chains data validation, transformation, and entity resolution into one adaptive pipeline. 4 MCP tools for listing stages, validating wiring, explaining configs, and running pipelines (file or inline data, returning per-stage results + the deduped output). Skips unnecessary stages automatically.",
            "homepage": "https://github.com/benseverndev-oss/goldenpipe",
            "iconUrl": "https://avatars.githubusercontent.com/u/192581748"
        })

    starlette_app = Starlette(
        debug=False,
        routes=[
            Route("/.well-known/mcp/server-card.json", server_card),
            Mount("/mcp", app=session_manager.handle_request),
        ],
        lifespan=lifespan,
    )

    uvicorn.run(starlette_app, host=host, port=port)
