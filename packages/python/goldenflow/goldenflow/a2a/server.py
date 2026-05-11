"""A2A protocol server for GoldenFlow (aiohttp) -- port 8150."""
from __future__ import annotations

import json

try:
    from aiohttp import web
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False


AGENT_CARD = {
    "name": "GoldenFlow",
    "description": "Data transformation -- standardize, clean, and normalize data with auto-detection and domain-aware transforms",
    "provider": {"organization": "Golden Suite"},
    "version": "1.0.0",
    "url": "http://localhost:8150",
    "skills": [
        {
            "id": "transform-data",
            "name": "Transform Data",
            "description": "Full transform workflow: profile data, apply transforms (zero-config or config-driven), return manifest of changes",
            "inputModes": ["text"],
            "outputModes": ["text"],
        },
        {
            "id": "map-schemas",
            "name": "Map Schemas",
            "description": "Auto-map columns between source and target datasets with confidence scores",
            "inputModes": ["text"],
            "outputModes": ["text"],
        },
        {
            "id": "discover",
            "name": "Discover Capabilities",
            "description": "List all available transforms and domain packs",
            "inputModes": ["text"],
            "outputModes": ["text"],
        },
        {
            "id": "diff-results",
            "name": "Diff Results",
            "description": "Compare before and after datasets to show what changed",
            "inputModes": ["text"],
            "outputModes": ["text"],
        },
        {
            "id": "configure",
            "name": "Configure",
            "description": "Auto-generate transform config from data patterns, with profile-based recommendations",
            "inputModes": ["text"],
            "outputModes": ["text"],
        },
        {
            "id": "handoff",
            "name": "Handoff from GoldenCheck",
            "description": "Map GoldenCheck findings to GoldenFlow transforms -- bridge for Check-to-Flow pipeline",
            "inputModes": ["text"],
            "outputModes": ["text"],
        },
    ],
}


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/.well-known/agent.json", agent_card)
    app.router.add_get("/health", health)
    app.router.add_post("/tasks", handle_task)
    return app


async def agent_card(request: web.Request) -> web.Response:
    return web.json_response(AGENT_CARD)


async def health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "version": "1.0.0"})


async def handle_task(request: web.Request) -> web.Response:
    body = await request.json()
    skill_id = body.get("skill", "")
    params = body.get("params", {})

    from goldenflow.mcp.server import handle_tool

    if skill_id == "transform-data":
        # Workflow: profile then transform
        result_parts = []
        if "path" in params:
            profile = handle_tool("profile", {"path": params["path"]})
            result_parts.append({"step": "profile", "result": json.loads(profile)})
            transform = handle_tool("transform", params)
            result_parts.append({"step": "transform", "result": json.loads(transform)})
        result = result_parts

    elif skill_id == "map-schemas":
        result = json.loads(handle_tool("map", params))

    elif skill_id == "discover":
        transforms = json.loads(handle_tool("list_transforms", {}))
        domains = json.loads(handle_tool("list_domains", {}))
        result = {"transforms": transforms, "domains": domains}

    elif skill_id == "diff-results":
        result = json.loads(handle_tool("diff", params))

    elif skill_id == "configure":
        result_parts = []
        if "path" in params:
            profile = handle_tool("profile", {"path": params["path"]})
            result_parts.append({"step": "profile", "result": json.loads(profile)})
            config = handle_tool("learn", {"path": params["path"]})
            result_parts.append({"step": "config", "result": json.loads(config)})
        result = result_parts

    elif skill_id == "handoff":
        result = json.loads(handle_tool("select_from_findings", params))

    else:
        result = {"error": f"Unknown skill: {skill_id}"}

    return web.json_response({
        "id": body.get("id", ""),
        "status": "completed",
        "result": result,
    })


def run_server(port: int = 8150) -> None:
    if not HAS_AIOHTTP:
        raise ImportError("aiohttp not installed. Run: pip install goldenflow[agent]")
    web.run_app(create_app(), port=port)
