"""A2A protocol server for GoldenPipe (aiohttp)."""
from __future__ import annotations


try:
    from aiohttp import web
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False


AGENT_CARD = {
    "name": "GoldenPipe",
    "description": "Pluggable pipeline framework for data quality workflows",
    "provider": {"organization": "Golden Suite"},
    "version": "1.0.0",
    "url": "http://localhost:8250",
    "skills": [
        {
            "id": "run-pipeline",
            "name": "Run Pipeline",
            "description": "Execute a data quality pipeline",
            "inputModes": ["text"],
            "outputModes": ["text"],
        },
        {
            "id": "validate-pipeline",
            "name": "Validate Pipeline",
            "description": "Validate pipeline wiring without executing",
            "inputModes": ["text"],
            "outputModes": ["text"],
        },
        {
            "id": "list-stages",
            "name": "List Stages",
            "description": "List all discovered pipeline stages",
            "inputModes": ["text"],
            "outputModes": ["text"],
        },
        {
            "id": "explain-pipeline",
            "name": "Explain Pipeline",
            "description": "Describe what a pipeline config will do",
            "inputModes": ["text"],
            "outputModes": ["text"],
        },
    ],
}


def create_app() -> "web.Application":
    app = web.Application()
    app.router.add_get("/.well-known/agent.json", agent_card)
    app.router.add_get("/health", health)
    app.router.add_post("/tasks", handle_task)
    return app


async def agent_card(request: "web.Request") -> "web.Response":
    return web.json_response(AGENT_CARD)


async def health(request: "web.Request") -> "web.Response":
    return web.json_response({"status": "ok", "version": "1.0.0"})


async def handle_task(request: "web.Request") -> "web.Response":
    body = await request.json()
    skill_id = body.get("skill", "")
    params = body.get("params", {})

    if skill_id == "list-stages":
        from goldenpipe.mcp.server import list_stages_tool
        result = list_stages_tool()
    elif skill_id == "validate-pipeline":
        from goldenpipe.mcp.server import validate_pipeline_tool
        result = validate_pipeline_tool(**params)
    elif skill_id == "run-pipeline":
        from goldenpipe.mcp.server import run_pipeline_tool
        result = run_pipeline_tool(**params)
    elif skill_id == "explain-pipeline":
        from goldenpipe.mcp.server import explain_pipeline_tool
        result = explain_pipeline_tool(**params)
    else:
        result = {"error": f"Unknown skill: {skill_id}"}

    return web.json_response({
        "id": body.get("id", ""),
        "status": "completed",
        "result": result,
    })


def run_server(port: int = 8250) -> None:
    if not HAS_AIOHTTP:
        raise ImportError("aiohttp not installed. Run: pip install goldenpipe[agent]")
    web.run_app(create_app(), port=port)
