"""A2A (Agent-to-Agent) protocol server for GoldenCheck — aiohttp-based."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid

from aiohttp import web

from goldencheck.a2a.skills import dispatch_skill

logger = logging.getLogger(__name__)

__all__ = ["AGENT_CARD", "create_a2a_app", "run_a2a_server"]

# ---------------------------------------------------------------------------
# Agent Card
# ---------------------------------------------------------------------------

AGENT_CARD: dict = {
    "name": "goldencheck-agent",
    "description": (
        "Autonomous data quality agent. Profiles data, discovers validation "
        "rules, detects anomalies, explains findings, manages "
        "confidence-gated review queue."
    ),
    "url": "http://localhost:8100",
    "version": "1.0.0",
    "provider": {
        "organization": "GoldenCheck",
        "url": "https://github.com/benzsevern/goldencheck",
    },
    "capabilities": {"streaming": True, "pushNotifications": False},
    "skills": [
        {
            "id": "analyze_data",
            "name": "Analyze Data",
            "description": (
                "Profile columns, detect domain, recommend profiling strategy"
            ),
            "inputModes": ["application/json"],
            "outputModes": ["application/json"],
        },
        {
            "id": "configure",
            "name": "Auto-Configure",
            "description": (
                "Generate optimal goldencheck.yml from data analysis"
            ),
            "inputModes": ["application/json"],
            "outputModes": ["application/json", "text/yaml"],
        },
        {
            "id": "scan",
            "name": "Scan",
            "description": (
                "Run full profiling pipeline with confidence-gated output"
            ),
            "inputModes": ["application/json"],
            "outputModes": ["application/json"],
        },
        {
            "id": "validate",
            "name": "Validate",
            "description": "Validate against pinned rules",
            "inputModes": ["application/json"],
            "outputModes": ["application/json"],
        },
        {
            "id": "explain",
            "name": "Explain Finding",
            "description": "Natural language explanation for a finding",
            "inputModes": ["application/json"],
            "outputModes": ["application/json", "text/plain"],
        },
        {
            "id": "review",
            "name": "Review Queue",
            "description": "Present borderline findings for approval",
            "inputModes": ["application/json"],
            "outputModes": ["application/json"],
        },
        {
            "id": "fix",
            "name": "Auto-Fix",
            "description": "Apply automated fixes",
            "inputModes": ["application/json"],
            "outputModes": ["application/json"],
        },
        {
            "id": "compare_domains",
            "name": "Compare Domains",
            "description": "Compare domain packs",
            "inputModes": ["application/json"],
            "outputModes": ["application/json"],
        },
        {
            "id": "handoff",
            "name": "Pipeline Handoff",
            "description": "Export quality attestation for downstream tools",
            "inputModes": ["application/json"],
            "outputModes": ["application/json"],
        },
    ],
    "authentication": {"schemes": ["bearer"]},
}

# ---------------------------------------------------------------------------
# In-memory task registry
# ---------------------------------------------------------------------------

_tasks: dict[str, dict] = {}

# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------


def _check_auth(request: web.Request) -> bool:
    """Return True if the request passes bearer-token auth.

    When ``GOLDENCHECK_AGENT_TOKEN`` is not set, all requests are allowed.
    """
    token = os.environ.get("GOLDENCHECK_AGENT_TOKEN")
    if not token:
        return True  # no auth required
    auth = request.headers.get("Authorization", "")
    return auth == f"Bearer {token}"


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------


async def _handle_agent_card(request: web.Request) -> web.Response:
    """GET /.well-known/agent.json"""
    card = dict(AGENT_CARD)
    # Reflect actual host/port from the request
    host = request.headers.get("Host", "localhost:8100")
    scheme = request.scheme
    card["url"] = f"{scheme}://{host}"
    return web.json_response(card)


async def _handle_tasks_send(request: web.Request) -> web.Response:
    """POST /tasks/send — synchronous task execution."""
    if not _check_auth(request):
        return web.json_response(
            {"error": "Unauthorized"}, status=401
        )

    try:
        body = await request.json()
    except (json.JSONDecodeError, Exception):
        return web.json_response(
            {"error": "Invalid JSON body"}, status=400
        )

    task_id = body.get("id", uuid.uuid4().hex)
    skill = body.get("skill", "")
    message = body.get("message", {})

    if not skill:
        return web.json_response(
            {"id": task_id, "state": "failed", "error": "Missing 'skill' field"},
            status=400,
        )

    # Register task as working
    _tasks[task_id] = {
        "id": task_id,
        "state": "working",
        "skill": skill,
        "result": None,
        "error": None,
    }

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, dispatch_skill, skill, message)

        if "error" in result and len(result) == 1:
            _tasks[task_id]["state"] = "failed"
            _tasks[task_id]["error"] = result["error"]
            return web.json_response({
                "id": task_id,
                "state": "failed",
                "error": result["error"],
            })

        _tasks[task_id]["state"] = "completed"
        _tasks[task_id]["result"] = result
        return web.json_response({
            "id": task_id,
            "state": "completed",
            "result": result,
        })

    except Exception as exc:
        logger.exception("Task %s failed", task_id)
        _tasks[task_id]["state"] = "failed"
        _tasks[task_id]["error"] = str(exc)
        return web.json_response(
            {"id": task_id, "state": "failed", "error": str(exc)},
            status=500,
        )


async def _handle_tasks_send_subscribe(request: web.Request) -> web.StreamResponse:
    """POST /tasks/sendSubscribe — SSE streaming task execution."""
    if not _check_auth(request):
        return web.json_response(
            {"error": "Unauthorized"}, status=401
        )

    try:
        body = await request.json()
    except (json.JSONDecodeError, Exception):
        return web.json_response(
            {"error": "Invalid JSON body"}, status=400
        )

    task_id = body.get("id", uuid.uuid4().hex)
    skill = body.get("skill", "")
    message = body.get("message", {})

    if not skill:
        return web.json_response(
            {"id": task_id, "state": "failed", "error": "Missing 'skill' field"},
            status=400,
        )

    # Register task
    _tasks[task_id] = {
        "id": task_id,
        "state": "working",
        "skill": skill,
        "result": None,
        "error": None,
    }

    # Set up SSE response
    response = web.StreamResponse()
    response.content_type = "text/event-stream"
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Connection"] = "keep-alive"
    await response.prepare(request)

    # Send working status
    working_event = _sse_encode("task-status", {
        "id": task_id,
        "state": "working",
        "progress": f"Executing skill '{skill}'...",
    })
    await response.write(working_event.encode("utf-8"))

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, dispatch_skill, skill, message)

        if "error" in result and len(result) == 1:
            _tasks[task_id]["state"] = "failed"
            _tasks[task_id]["error"] = result["error"]
            error_event = _sse_encode("task-status", {
                "id": task_id,
                "state": "failed",
                "error": result["error"],
            })
            await response.write(error_event.encode("utf-8"))
        else:
            _tasks[task_id]["state"] = "completed"
            _tasks[task_id]["result"] = result

            # Send artifact
            artifact_event = _sse_encode("task-artifact", {
                "id": task_id,
                "artifact": {
                    "type": "result",
                    "parts": [{"type": "data", "data": result}],
                },
            })
            await response.write(artifact_event.encode("utf-8"))

            # Send completed status
            completed_event = _sse_encode("task-status", {
                "id": task_id,
                "state": "completed",
            })
            await response.write(completed_event.encode("utf-8"))

    except Exception as exc:
        logger.exception("Task %s failed", task_id)
        _tasks[task_id]["state"] = "failed"
        _tasks[task_id]["error"] = str(exc)
        error_event = _sse_encode("task-status", {
            "id": task_id,
            "state": "failed",
            "error": str(exc),
        })
        await response.write(error_event.encode("utf-8"))

    await response.write_eof()
    return response


async def _handle_task_get(request: web.Request) -> web.Response:
    """GET /tasks/{id} — return task state from registry."""
    task_id = request.match_info["id"]
    task = _tasks.get(task_id)
    if task is None:
        return web.json_response(
            {"error": f"Task {task_id!r} not found"}, status=404
        )
    return web.json_response(task)


async def _handle_task_cancel(request: web.Request) -> web.Response:
    """POST /tasks/{id}/cancel — best-effort cancellation."""
    if not _check_auth(request):
        return web.json_response(
            {"error": "Unauthorized"}, status=401
        )

    task_id = request.match_info["id"]
    task = _tasks.get(task_id)
    if task is None:
        return web.json_response(
            {"error": f"Task {task_id!r} not found"}, status=404
        )

    # Best-effort: only cancel if still working
    if task["state"] == "working":
        task["state"] = "canceled"
    return web.json_response(task)


# ---------------------------------------------------------------------------
# SSE helper
# ---------------------------------------------------------------------------


def _sse_encode(event: str, data: dict) -> str:
    """Encode an SSE event with JSON data."""
    payload = json.dumps(data, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_a2a_app(port: int = 8100) -> web.Application:
    """Create and return an aiohttp app with all A2A routes registered."""
    app = web.Application()
    app["port"] = port

    app.router.add_get("/.well-known/agent.json", _handle_agent_card)
    app.router.add_post("/tasks/send", _handle_tasks_send)
    app.router.add_post("/tasks/sendSubscribe", _handle_tasks_send_subscribe)
    app.router.add_get("/tasks/{id}", _handle_task_get)
    app.router.add_post("/tasks/{id}/cancel", _handle_task_cancel)

    return app


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------


async def run_a2a_server(port: int = 8100) -> None:
    """Start the A2A server and block until interrupted."""
    app = create_a2a_app(port)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info("GoldenCheck A2A server running on http://0.0.0.0:%d", port)
    # Keep running until interrupted
    await asyncio.Event().wait()
