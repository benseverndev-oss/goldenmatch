"""MCP server exposing InferMap tools for Claude Desktop and other MCP clients."""
from __future__ import annotations

import json
import logging

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

logger = logging.getLogger("infermap.mcp")

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS = [
    Tool(
        name="map",
        description=(
            "Map source columns to target schema using a weighted scorer pipeline "
            "with optimal 1:1 assignment. Returns mappings with confidence scores "
            "and human-readable reasoning."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "Path to source data (CSV, Parquet, Excel, DB URI, schema YAML)",
                },
                "target": {
                    "type": "string",
                    "description": "Path to target data (same variety of inputs)",
                },
                "table": {
                    "type": "string",
                    "description": "Table name for DB sources (optional)",
                },
                "schema_file": {
                    "type": "string",
                    "description": "Path to schema definition YAML file (optional)",
                },
                "min_confidence": {
                    "type": "number",
                    "description": "Minimum confidence threshold (default 0.2)",
                    "default": 0.2,
                },
                "domains": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Domain dictionaries to load (e.g. ['healthcare', 'finance'])",
                },
            },
            "required": ["source", "target"],
        },
    ),
    Tool(
        name="inspect",
        description=(
            "Inspect a data source — show fields, types, sample values, null rates, "
            "unique rates, and statistics."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "Path to data source (CSV, Parquet, Excel, DB URI, schema YAML)",
                },
                "table": {
                    "type": "string",
                    "description": "Table name for DB sources (optional)",
                },
            },
            "required": ["source"],
        },
    ),
    Tool(
        name="validate",
        description=(
            "Validate that a source file's columns satisfy a saved mapping config. "
            "Reports missing source columns and unmapped required fields."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "Path to source data file",
                },
                "config": {
                    "type": "string",
                    "description": "Path to mapping config YAML file",
                },
                "required_fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Target field names that must be mapped",
                },
            },
            "required": ["source", "config"],
        },
    ),
    Tool(
        name="apply",
        description=(
            "Apply a saved mapping config to a source CSV, renaming columns "
            "according to the mapping and writing the result to an output file."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "Path to source CSV file",
                },
                "config": {
                    "type": "string",
                    "description": "Path to mapping config YAML file",
                },
                "output": {
                    "type": "string",
                    "description": "Output CSV file path",
                },
            },
            "required": ["source", "config", "output"],
        },
    ),
]


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

def _handle_map(args: dict) -> dict:
    from infermap.engine import MapEngine

    kwargs: dict = {}
    if args.get("table"):
        kwargs["table"] = args["table"]

    engine = MapEngine(
        min_confidence=args.get("min_confidence", 0.2),
        domains=args.get("domains"),
    )
    result = engine.map(
        args["source"],
        args["target"],
        schema_file=args.get("schema_file"),
        **kwargs,
    )
    return result.report()


def _handle_inspect(args: dict) -> dict:
    from infermap.providers import extract_schema

    kwargs: dict = {}
    if args.get("table"):
        kwargs["table"] = args["table"]

    schema = extract_schema(args["source"], **kwargs)
    return {
        "source_name": schema.source_name or args["source"],
        "field_count": len(schema.fields),
        "fields": [
            {
                "name": f.name,
                "dtype": f.dtype,
                "null_rate": round(f.null_rate, 4),
                "unique_rate": round(f.unique_rate, 4),
                "value_count": f.value_count,
                "sample_values": [str(s) for s in f.sample_values[:5]],
            }
            for f in schema.fields
        ],
    }


def _handle_validate(args: dict) -> dict:
    from infermap.config import from_config
    from infermap.providers import extract_schema

    mapping_result = from_config(args["config"])
    schema = extract_schema(args["source"])
    source_cols = {f.name for f in schema.fields}

    missing_sources = [m.source for m in mapping_result.mappings if m.source not in source_cols]
    mapped_targets = {m.target for m in mapping_result.mappings}

    required_fields = args.get("required_fields", [])
    missing_required = [r for r in required_fields if r not in mapped_targets]

    return {
        "all_sources_present": len(missing_sources) == 0,
        "missing_sources": missing_sources,
        "required_fields_mapped": len(missing_required) == 0,
        "missing_required": missing_required,
        "mapping_count": len(mapping_result.mappings),
    }


def _handle_apply(args: dict) -> dict:
    from infermap.config import from_config

    mapping_result = from_config(args["config"])

    import polars as pl
    df = pl.read_csv(args["source"], encoding="utf8-lossy")
    df_out = mapping_result.apply(df)
    df_out.write_csv(args["output"])

    return {
        "rows_written": len(df_out),
        "columns": list(df_out.columns),
        "output": args["output"],
    }


HANDLERS = {
    "map": _handle_map,
    "inspect": _handle_inspect,
    "validate": _handle_validate,
    "apply": _handle_apply,
}


# ---------------------------------------------------------------------------
# Server factory
# ---------------------------------------------------------------------------

def create_server() -> Server:
    server = Server("infermap")

    @server.list_tools()
    async def list_tools():
        return TOOLS

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        handler = HANDLERS.get(name)
        if not handler:
            result = {"error": f"Unknown tool: {name}"}
        else:
            try:
                result = handler(arguments)
            except Exception as exc:
                logger.exception("Tool %s failed", name)
                result = {"error": str(exc)}

        return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

    return server


async def run_server() -> None:
    server = create_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def run_server_http(host: str = "0.0.0.0", port: int = 8100) -> None:
    """Run the MCP server over Streamable HTTP (for Railway / remote deployment)."""
    import contextlib
    from collections.abc import AsyncIterator

    import uvicorn
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Mount, Route
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

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
            "name": "InferMap",
            "description": "Schema mapping engine — 7 scorers, domain dictionaries, confidence calibration. F1 0.84 on 162 real-world cases. Python + TypeScript.",
            "homepage": "https://github.com/benzsevern/infermap",
            "iconUrl": "https://avatars.githubusercontent.com/u/198941534",
        })

    starlette_app = Starlette(
        debug=False,
        routes=[
            Route("/.well-known/mcp/server-card.json", server_card),
            Mount("/mcp", app=session_manager.handle_request),
        ],
        lifespan=lifespan,
    )

    logger.info("Starting MCP HTTP server on %s:%s", host, port)
    uvicorn.run(starlette_app, host=host, port=port)
