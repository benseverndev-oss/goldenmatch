"""MCP server for GoldenFlow -- 10 tools for data transformation."""
from __future__ import annotations

import json
from pathlib import Path

TOOLS = [
    {
        "name": "transform",
        "description": "Transform a data file using GoldenFlow. Zero-config or config-driven.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to data file"},
                "config": {"type": "string", "description": "Optional YAML config path"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "map",
        "description": "Auto-map schemas between source and target files.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "Source file path"},
                "target": {"type": "string", "description": "Target file path"},
            },
            "required": ["source", "target"],
        },
    },
    {
        "name": "profile",
        "description": "Profile a data file showing column types, nulls, and patterns.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to data file"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "learn",
        "description": "Generate a YAML config from data patterns.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to data file"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "diff",
        "description": "Compare two data files and show what changed (added, removed, modified rows).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path_before": {"type": "string", "description": "Path to original file"},
                "path_after": {"type": "string", "description": "Path to transformed file"},
            },
            "required": ["path_before", "path_after"],
        },
    },
    {
        "name": "validate",
        "description": "Dry-run transform on a file. Shows what would change without writing output.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to data file"},
                "config": {"type": "string", "description": "Optional YAML config path"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "list_transforms",
        "description": "List all registered transforms with their modes, input types, and auto-apply status.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "explain_transform",
        "description": "Describe what a specific transform does, its mode, and input types.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "transform_name": {"type": "string", "description": "Name of the transform"},
            },
            "required": ["transform_name"],
        },
    },
    {
        "name": "list_domains",
        "description": "List available domain packs (e.g., people_hr, ecommerce, finance).",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "select_from_findings",
        "description": "Map GoldenCheck findings to recommended GoldenFlow transforms. Bridge tool for Check-to-Flow handoff.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "findings": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "List of GoldenCheck findings (each with 'check' and 'column' fields)",
                },
            },
            "required": ["findings"],
        },
    },
]


def handle_tool(name: str, arguments: dict) -> str:
    """Handle an MCP tool call."""
    from goldenflow.connectors.file import read_file
    from goldenflow.engine.profiler_bridge import profile_dataframe
    from goldenflow.engine.transformer import TransformEngine

    if name == "transform":
        path = Path(arguments["path"])
        config = None
        if "config" in arguments:
            from goldenflow.config.loader import load_config
            config = load_config(Path(arguments["config"]))
        engine = TransformEngine(config=config)
        result = engine.transform_file(path)
        return json.dumps(result.manifest.to_dict(), indent=2)

    elif name == "map":
        from goldenflow.mapping.schema_mapper import SchemaMapper
        source_df = read_file(Path(arguments["source"]))
        target_df = read_file(Path(arguments["target"]))
        mapper = SchemaMapper()
        mappings = mapper.map(source_df, target_df)
        return json.dumps([
            {"source": m.source, "target": m.target, "confidence": m.confidence}
            for m in mappings
        ], indent=2)

    elif name == "profile":
        df = read_file(Path(arguments["path"]))
        profile = profile_dataframe(df, file_path=arguments["path"])
        return json.dumps({
            "rows": profile.row_count,
            "columns": [
                {"name": c.name, "type": c.inferred_type, "nulls": c.null_count}
                for c in profile.columns
            ],
        }, indent=2)

    elif name == "learn":
        from goldenflow.config.learner import learn_config
        config = learn_config(Path(arguments["path"]))
        return config.model_dump_json(indent=2)

    elif name == "diff":
        from goldenflow.engine.differ import diff_dataframes
        df_before = read_file(Path(arguments["path_before"]))
        df_after = read_file(Path(arguments["path_after"]))
        result = diff_dataframes(df_before, df_after)
        return json.dumps({
            "added_rows": result.added,
            "removed_rows": result.removed,
            "modified_rows": result.modified,
            "unchanged_rows": result.unchanged,
            "columns_added": result.columns_added,
            "columns_removed": result.columns_removed,
        }, indent=2, default=str)

    elif name == "validate":
        path = Path(arguments["path"])
        config = None
        if "config" in arguments:
            from goldenflow.config.loader import load_config
            config = load_config(Path(arguments["config"]))
        engine = TransformEngine(config=config)
        result = engine.transform_df(read_file(path), source=str(path))
        records = result.manifest.records if hasattr(result.manifest, "records") else []
        return json.dumps({
            "would_apply": [
                {"column": r.column, "transform": r.transform, "rows_affected": r.rows_affected}
                for r in records
            ],
            "total_transforms": len(records),
        }, indent=2, default=str)

    elif name == "list_transforms":
        from goldenflow.transforms import list_transforms
        transforms = list_transforms()
        return json.dumps([
            {
                "name": t.name,
                "mode": t.mode,
                "input_types": t.input_types,
                "auto_apply": t.auto_apply,
                "priority": t.priority,
            }
            for t in transforms
        ], indent=2)

    elif name == "explain_transform":
        from goldenflow.transforms import get_transform
        t = get_transform(arguments["transform_name"])
        if t is None:
            return json.dumps({"error": f"Transform '{arguments['transform_name']}' not found"})
        desc = getattr(t, "description", None) or getattr(getattr(t, "fn", None), "__doc__", None) or "No description available"
        return json.dumps({
            "name": t.name,
            "mode": t.mode,
            "input_types": t.input_types,
            "auto_apply": t.auto_apply,
            "priority": t.priority,
            "description": desc,
        }, indent=2)

    elif name == "list_domains":
        from goldenflow.domains import load_domain
        domain_names = ["base", "people_hr", "ecommerce", "finance", "healthcare", "real_estate"]
        domains = []
        for d in domain_names:
            try:
                pack = load_domain(d)
                domains.append({
                    "name": d,
                    "description": getattr(pack, "description", ""),
                    "transform_count": len(getattr(pack, "transforms", [])),
                })
            except Exception:
                pass
        return json.dumps(domains, indent=2)

    elif name == "select_from_findings":
        from goldenflow.engine.selector import select_from_findings
        findings = arguments["findings"]
        selected = select_from_findings(findings)
        return json.dumps([
            {"column": s.get("column", ""), "transform": s.get("transform", ""), "reason": s.get("reason", "")}
            for s in selected
        ], indent=2, default=str)

    return json.dumps({"error": f"Unknown tool: {name}"})


def create_server():
    """Create and return a configured MCP Server instance."""
    try:
        from mcp.server import Server
    except ImportError:
        raise ImportError("MCP server requires: pip install goldenflow[mcp]")

    server = Server("GoldenFlow")

    @server.list_tools()
    async def list_tools():
        from mcp.types import Tool
        return [Tool(**t) for t in TOOLS]

    @server.list_resources()
    async def list_resources():
        return []

    @server.list_prompts()
    async def list_prompts():
        return []

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        result = handle_tool(name, arguments)
        return [{"type": "text", "text": result}]

    return server


def run_server():
    """Run the MCP server over stdio. Requires mcp package."""
    from mcp.server.stdio import stdio_server

    import asyncio

    server = create_server()
    asyncio.run(stdio_server(server))


def run_server_http(host: str = "0.0.0.0", port: int = 8150):
    """Run the MCP server over Streamable HTTP transport."""
    import contextlib
    from collections.abc import AsyncIterator

    import uvicorn
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Mount, Route

    server = create_server()
    session_manager = StreamableHTTPSessionManager(app=server, stateless=True)

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        async with session_manager.run():
            yield

    async def server_card(request):
        return JSONResponse({
            "name": "GoldenFlow",
            "description": "Data transformation toolkit — standardize, reshape, and normalize messy data automatically. 76 built-in transforms across 11 categories (text, phone, name, address, date, categorical, numeric, email, identifiers, URL, auto-correct). 10 MCP tools for transforming, profiling, mapping schemas, and applying domain packs. DQBench Transform Score: 100/100. Built on Polars.",
            "homepage": "https://github.com/benzsevern/goldenflow",
            "iconUrl": "https://avatars.githubusercontent.com/u/192581748"
        })

    starlette_app = Starlette(
        lifespan=lifespan,
        routes=[
            Route("/.well-known/mcp/server-card.json", server_card),
            Mount("/mcp", app=session_manager.handle_request),
        ],
    )

    uvicorn.run(starlette_app, host=host, port=port)
