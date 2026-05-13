"""MCP tools for the Identity Graph.

Five tools:

- ``identity_resolve``   -> look up an identity by record_id
- ``identity_history``   -> event log for an entity
- ``identity_conflicts`` -> conflicting evidence edges
- ``identity_merge``     -> manually merge two identities
- ``identity_split``     -> split records into a new identity
"""
from __future__ import annotations

import json
import logging
from typing import Any

from mcp.types import TextContent, Tool

from goldenmatch.identity import (
    IdentityStore,
    find_by_record,
    find_conflicts,
    history,
    list_entities,
    manual_merge,
    manual_split,
)

logger = logging.getLogger(__name__)

_DEFAULT_PATH = ".goldenmatch/identity.db"


IDENTITY_TOOLS: list[Tool] = [
    Tool(
        name="identity_resolve",
        description=(
            "Resolve a record_id to its durable identity. Returns the full "
            "identity view (members, evidence edges, recent events) or null "
            "when no identity exists for that record."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "record_id": {
                    "type": "string",
                    "description": "record id in `{source}:{source_pk}` form",
                },
                "path": {"type": "string", "description": "Identity DB path"},
            },
            "required": ["record_id"],
        },
    ),
    Tool(
        name="identity_list",
        description="List identities, optionally filtered by dataset/status.",
        inputSchema={
            "type": "object",
            "properties": {
                "dataset": {"type": "string"},
                "status": {"type": "string"},
                "limit": {"type": "integer", "default": 50},
                "offset": {"type": "integer", "default": 0},
                "path": {"type": "string"},
            },
        },
    ),
    Tool(
        name="identity_history",
        description="Return the temporal event log for an identity.",
        inputSchema={
            "type": "object",
            "properties": {
                "entity_id": {"type": "string"},
                "limit": {"type": "integer", "default": 100},
                "path": {"type": "string"},
            },
            "required": ["entity_id"],
        },
    ),
    Tool(
        name="identity_conflicts",
        description="List evidence edges marked `conflicts_with`.",
        inputSchema={
            "type": "object",
            "properties": {
                "dataset": {"type": "string"},
                "path": {"type": "string"},
            },
        },
    ),
    Tool(
        name="identity_merge",
        description=(
            "Manually merge two identities. All records from "
            "`absorb_entity_id` are reassigned to `keep_entity_id`."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "keep_entity_id": {"type": "string"},
                "absorb_entity_id": {"type": "string"},
                "reason": {"type": "string"},
                "path": {"type": "string"},
            },
            "required": ["keep_entity_id", "absorb_entity_id"],
        },
    ),
    Tool(
        name="identity_split",
        description=(
            "Split a subset of records off an identity into a brand-new "
            "identity. The original keeps the remaining records."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "entity_id": {"type": "string"},
                "record_ids": {"type": "array", "items": {"type": "string"}},
                "reason": {"type": "string"},
                "path": {"type": "string"},
            },
            "required": ["entity_id", "record_ids"],
        },
    ),
]


IDENTITY_TOOL_NAMES = frozenset(t.name for t in IDENTITY_TOOLS)


def _open(args: dict) -> IdentityStore:
    return IdentityStore(path=args.get("path") or _DEFAULT_PATH)


def _dispatch(name: str, args: dict) -> dict[str, Any]:
    if name == "identity_resolve":
        with _open(args) as s:
            view = find_by_record(s, args["record_id"])
        return view.to_dict() if view else {"found": False}

    if name == "identity_list":
        with _open(args) as s:
            items = list_entities(
                s,
                dataset=args.get("dataset"),
                status=args.get("status"),
                limit=int(args.get("limit", 50)),
                offset=int(args.get("offset", 0)),
            )
        return {"items": items}

    if name == "identity_history":
        with _open(args) as s:
            events = history(s, args["entity_id"], limit=int(args.get("limit", 100)))
        return {"items": events}

    if name == "identity_conflicts":
        with _open(args) as s:
            edges = find_conflicts(s, dataset=args.get("dataset"))
        return {"items": edges}

    if name == "identity_merge":
        with _open(args) as s:
            return manual_merge(
                s,
                keep_entity_id=args["keep_entity_id"],
                absorb_entity_id=args["absorb_entity_id"],
                reason=args.get("reason"),
                run_name="mcp",
            )

    if name == "identity_split":
        with _open(args) as s:
            return manual_split(
                s,
                entity_id=args["entity_id"],
                record_ids=list(args["record_ids"]),
                reason=args.get("reason"),
                run_name="mcp",
            )

    raise ValueError(f"unknown identity tool: {name}")


async def handle_identity_tool(name: str, args: dict) -> list[TextContent]:
    """Async wrapper for direct MCP server registration."""
    payload = _dispatch(name, args)
    return [TextContent(type="text", text=json.dumps(payload, default=str))]
