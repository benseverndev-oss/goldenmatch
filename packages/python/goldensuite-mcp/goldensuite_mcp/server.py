"""Aggregator MCP server that exposes every Golden Suite tool from one endpoint.

Each sub-package exposes (via its mcp.server module):
- TOOLS: list[mcp.types.Tool] OR list[dict] (goldenflow uses dicts)
- a dispatcher callable taking (name, args) -> dict (handler returns a JSON-serializable dict)

The aggregator imports those, normalizes Tool format, applies first-wins on name
collisions, and builds one Server that routes call_tool to the originating
sub-package's dispatcher.

Tool collisions are logged WARNING at server creation so deployers can see
which tool shadowed which; the user explicitly opted into first-wins (no
package prefixing) so this is an information-only signal.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from mcp.server import Server
from mcp.types import TextContent, Tool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sub-package adapters
# ---------------------------------------------------------------------------
# Each adapter returns (tools, dispatch). Tools are normalized to mcp.types.Tool.
# Dispatch is a Callable[[str, dict], dict] — handler returns a serializable dict.


def _normalize_tools(raw_tools: list) -> list[Tool]:
    """Accept either Tool objects or dicts (goldenflow style); return Tool list."""
    out: list[Tool] = []
    for t in raw_tools:
        if isinstance(t, Tool):
            out.append(t)
        elif isinstance(t, dict):
            out.append(Tool(**t))
        else:
            logger.warning("Skipping unrecognized tool entry of type %s", type(t).__name__)
    return out


def _adapt_goldenmatch() -> tuple[list[Tool], Callable[[str, dict], dict]]:
    from goldenmatch.mcp import server as gm

    return _normalize_tools(list(gm.TOOLS)), gm.dispatch


def _adapt_goldencheck() -> tuple[list[Tool], Callable[[str, dict], dict]]:
    from goldencheck.mcp import server as gc

    handlers = gc._TOOL_HANDLERS

    def dispatch(name: str, args: dict) -> dict:
        h = handlers.get(name)
        if h is None:
            return {"error": f"goldencheck: unknown tool {name!r}"}
        return h(args)

    return _normalize_tools(list(gc.TOOLS)), dispatch


def _adapt_goldenflow() -> tuple[list[Tool], Callable[[str, dict], dict]]:
    from goldenflow.mcp import server as gf

    def dispatch(name: str, args: dict) -> dict:
        # goldenflow.handle_tool returns a JSON string, not a dict.
        raw = gf.handle_tool(name, args)
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return {"result": raw}

    return _normalize_tools(list(gf.TOOLS)), dispatch


def _adapt_goldenpipe() -> tuple[list[Tool], Callable[[str, dict], dict]]:
    from goldenpipe.mcp import server as gp

    handlers = gp.HANDLERS

    def dispatch(name: str, args: dict) -> dict:
        h = handlers.get(name)
        if h is None:
            return {"error": f"goldenpipe: unknown tool {name!r}"}
        return h(args)

    return _normalize_tools(list(gp.TOOLS)), dispatch


def _adapt_infermap() -> tuple[list[Tool], Callable[[str, dict], dict]]:
    from infermap.mcp import server as im

    handlers = im.HANDLERS

    def dispatch(name: str, args: dict) -> dict:
        h = handlers.get(name)
        if h is None:
            return {"error": f"infermap: unknown tool {name!r}"}
        return h(args)

    return _normalize_tools(list(im.TOOLS)), dispatch


# Order determines first-wins precedence on tool-name collisions.
# Goldenmatch is the headline package, so its tools win; the others register
# only their unshadowed names. Adjust this order to change precedence.
_SUITE_ORDER: list[tuple[str, Callable[[], tuple[list[Tool], Callable[[str, dict], dict]]]]] = [
    ("goldenmatch", _adapt_goldenmatch),
    ("goldencheck", _adapt_goldencheck),
    ("goldenflow", _adapt_goldenflow),
    ("goldenpipe", _adapt_goldenpipe),
    ("infermap", _adapt_infermap),
]


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def _aggregate() -> tuple[list[Tool], dict[str, Callable[[str, dict], dict]]]:
    """Compose all sub-packages' tools + dispatchers under first-wins."""
    all_tools: list[Tool] = []
    name_to_dispatch: dict[str, Callable[[str, dict], dict]] = {}
    seen: dict[str, str] = {}  # tool_name -> source pkg

    for source_name, adapter in _SUITE_ORDER:
        try:
            tools, dispatch = adapter()
        except Exception as exc:  # noqa: BLE001 — we want to keep going
            logger.warning(
                "Skipping %s in goldensuite-mcp: failed to load (%s: %s)",
                source_name, type(exc).__name__, exc,
            )
            continue

        added = 0
        for tool in tools:
            if tool.name in seen:
                logger.warning(
                    "tool collision: %r from %s shadowed by earlier %s (first-wins)",
                    tool.name, source_name, seen[tool.name],
                )
                continue
            seen[tool.name] = source_name
            all_tools.append(tool)
            name_to_dispatch[tool.name] = dispatch
            added += 1
        if not tools:
            # Sub-package import succeeded but its TOOLS list was empty. Most
            # commonly this means the sub-package's optional [mcp] extra wasn't
            # installed when it was imported (HAS_MCP=False -> TOOLS=[]).
            logger.warning(
                "goldensuite-mcp: %s registered 0 tools — is its [mcp] extra installed?",
                source_name,
            )
        else:
            logger.info("goldensuite-mcp: registered %d tools from %s", added, source_name)

    return all_tools, name_to_dispatch


def create_server() -> Server:
    """Build the aggregated Server."""
    tools, dispatch_by_name = _aggregate()

    server = Server("goldensuite-mcp")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return tools

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        handler = dispatch_by_name.get(name)
        if handler is None:
            payload: Any = {"error": f"unknown tool: {name}"}
        else:
            try:
                payload = handler(name, arguments or {})
            except Exception as exc:  # noqa: BLE001
                logger.exception("tool %s failed", name)
                payload = {"error": f"{type(exc).__name__}: {exc}"}
        return [TextContent(type="text", text=json.dumps(payload, default=str, indent=2))]

    return server


__all__ = ["create_server"]
