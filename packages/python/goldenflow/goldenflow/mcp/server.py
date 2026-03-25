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

    return json.dumps({"error": f"Unknown tool: {name}"})


def run_server():
    """Run the MCP server. Requires mcp package."""
    try:
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
    except ImportError:
        raise ImportError("MCP server requires: pip install goldenflow[mcp]")

    server = Server("goldenflow")

    @server.list_tools()
    async def list_tools():
        return TOOLS

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        result = handle_tool(name, arguments)
        return [{"type": "text", "text": result}]

    import asyncio
    asyncio.run(stdio_server(server))
