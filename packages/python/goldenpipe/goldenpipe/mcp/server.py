"""MCP server with pipeline tools."""
from __future__ import annotations

from typing import Any

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent
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


def run_pipeline_tool(source: str, config_path: str | None = None) -> dict[str, Any]:
    """Run a pipeline and return results."""
    from goldenpipe._api import run
    result = run(source, config=config_path)
    return {
        "status": result.status.value,
        "source": result.source,
        "input_rows": result.input_rows,
        "errors": result.errors,
        "skipped": result.skipped,
    }


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


def run_server() -> None:
    """Start the MCP server."""
    if not HAS_MCP:
        raise ImportError("MCP not installed. Run: pip install goldenpipe[mcp]")

    import asyncio
    import json

    server = Server("goldenpipe")

    @server.list_tools()
    async def handle_list_tools():
        return [
            Tool(name="list_stages", description="List all discovered pipeline stages",
                 inputSchema={"type": "object", "properties": {}}),
            Tool(name="validate_pipeline", description="Validate pipeline wiring",
                 inputSchema={"type": "object", "properties": {
                     "pipeline": {"type": "string"},
                     "stages": {"type": "array", "items": {"type": "string"}},
                 }, "required": ["pipeline", "stages"]}),
            Tool(name="run_pipeline", description="Run a pipeline on a file",
                 inputSchema={"type": "object", "properties": {
                     "source": {"type": "string"},
                     "config_path": {"type": "string"},
                 }, "required": ["source"]}),
            Tool(name="explain_pipeline", description="Explain what a pipeline config does",
                 inputSchema={"type": "object", "properties": {
                     "config_path": {"type": "string"},
                 }, "required": ["config_path"]}),
        ]

    @server.call_tool()
    async def handle_call_tool(name: str, arguments: dict):
        if name == "list_stages":
            result = list_stages_tool()
        elif name == "validate_pipeline":
            result = validate_pipeline_tool(**arguments)
        elif name == "run_pipeline":
            result = run_pipeline_tool(**arguments)
        elif name == "explain_pipeline":
            result = explain_pipeline_tool(**arguments)
        else:
            result = {"error": f"Unknown tool: {name}"}

        return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

    async def main():
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    asyncio.run(main())
