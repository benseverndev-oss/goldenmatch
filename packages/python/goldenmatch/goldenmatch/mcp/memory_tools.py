"""MCP tool surface for Learning Memory.

Five tools wrap MemoryStore + MemoryLearner operations:
  list_corrections, add_correction, learn_thresholds, memory_stats, memory_export.

Each handler instantiates its own MemoryStore (matches AgentSession pattern in
agent_tools.py -- no shared global state). All handlers trap
sqlite3.OperationalError and return a structured JSON error in TextContent
rather than raising, so a failed memory call cannot crash an MCP session.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime
from typing import Any

from mcp.types import Tool, TextContent

logger = logging.getLogger(__name__)


_DEFAULT_PATH = ".goldenmatch/memory.db"


MEMORY_TOOLS: list[Tool] = [
    Tool(
        name="list_corrections",
        description=(
            "List stored Learning Memory corrections, optionally filtered by "
            "dataset. Returns id_a, id_b, decision, source, trust, reason, "
            "matchkey_name, dataset, original_score, created_at."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "dataset": {
                    "type": "string",
                    "description": "Optional dataset filter (e.g. file path).",
                },
                "path": {
                    "type": "string",
                    "description": "SQLite memory DB path. Default: .goldenmatch/memory.db",
                },
            },
        },
    ),
    Tool(
        name="add_correction",
        description=(
            "Add a pair correction to Learning Memory. Source is set to 'agent' "
            "with trust=0.5 (lower than human steward decisions which are 1.0). "
            "Pair (id_a, id_b) is canonicalized to (min, max) before storage."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "id_a": {"type": "integer"},
                "id_b": {"type": "integer"},
                "decision": {
                    "type": "string",
                    "enum": ["approve", "reject"],
                },
                "dataset": {
                    "type": "string",
                    "description": "Dataset identifier (e.g. file path). Required, non-empty.",
                },
                "reason": {"type": "string"},
                "matchkey_name": {"type": "string"},
                "path": {
                    "type": "string",
                    "description": "SQLite memory DB path. Default: .goldenmatch/memory.db",
                },
            },
            "required": ["id_a", "id_b", "decision", "dataset"],
        },
    ),
    Tool(
        name="learn_thresholds",
        description=(
            "Force a MemoryLearner pass over accumulated corrections. Returns "
            "the list of LearnedAdjustments produced (matchkey_name, threshold, "
            "sample_size, learned_at). Requires >= 10 corrections per matchkey "
            "before threshold tuning fires; otherwise returns an empty list."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "matchkey_name": {
                    "type": "string",
                    "description": "Optional: learn only for this matchkey.",
                },
                "path": {
                    "type": "string",
                    "description": "SQLite memory DB path. Default: .goldenmatch/memory.db",
                },
            },
        },
    ),
    Tool(
        name="memory_stats",
        description=(
            "Return Learning Memory status: total correction count, last learn "
            "time, and current learned adjustments. Cheap; safe for status checks."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "SQLite memory DB path. Default: .goldenmatch/memory.db",
                },
            },
        },
    ),
    Tool(
        name="memory_export",
        description=(
            "Return all corrections as a list of dicts (CSV-shaped). Caller is "
            "responsible for writing the file. Optionally filter by dataset."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "dataset": {"type": "string"},
                "path": {
                    "type": "string",
                    "description": "SQLite memory DB path. Default: .goldenmatch/memory.db",
                },
            },
        },
    ),
]


_MEMORY_TOOL_NAMES: frozenset[str] = frozenset(t.name for t in MEMORY_TOOLS)


def _correction_to_dict(c: Any) -> dict:
    return {
        "id": c.id,
        "id_a": c.id_a,
        "id_b": c.id_b,
        "decision": c.decision,
        "source": c.source,
        "trust": c.trust,
        "field_hash": c.field_hash,
        "record_hash": c.record_hash,
        "original_score": c.original_score,
        "matchkey_name": c.matchkey_name,
        "reason": c.reason,
        "dataset": c.dataset,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


def _adjustment_to_dict(a: Any) -> dict:
    return {
        "matchkey_name": a.matchkey_name,
        "threshold": a.threshold,
        "field_weights": a.field_weights,
        "sample_size": a.sample_size,
        "learned_at": a.learned_at.isoformat() if a.learned_at else None,
    }


# Re-export point for tests/monkeypatching: the dispatcher reads MemoryStore
# from this module so tests can swap it out.
from goldenmatch.core.memory.store import MemoryStore as MemoryStore  # noqa: E402,F401


def handle_memory_tool(name: str, arguments: dict) -> list[TextContent]:
    """Route a memory-tool MCP call to its handler.

    Each handler opens its own MemoryStore. Returns JSON in TextContent.
    sqlite3.OperationalError (and other exceptions) are trapped and returned
    as a structured error rather than raised.
    """
    try:
        result = _dispatch(name, arguments)
    except sqlite3.OperationalError as exc:
        logger.warning("Memory tool %s sqlite error: %s", name, exc)
        result = {"error": f"sqlite3.OperationalError: {exc}"}
    except Exception as exc:
        logger.exception("Memory tool %s failed", name)
        result = {"error": str(exc)}

    return [TextContent(
        type="text",
        text=json.dumps(result, default=str, indent=2),
    )]


def _dispatch(name: str, args: dict) -> dict:
    path = args.get("path") or _DEFAULT_PATH

    if name == "list_corrections":
        dataset = args.get("dataset")
        with MemoryStore(backend="sqlite", path=path) as store:
            corrections = store.get_corrections(dataset=dataset)
        return {
            "count": len(corrections),
            "corrections": [_correction_to_dict(c) for c in corrections],
        }

    if name == "add_correction":
        # Validate required, non-empty dataset.
        dataset = args.get("dataset")
        if not dataset:
            return {"error": "Missing or empty required parameter: dataset"}

        decision = args.get("decision")
        if decision not in ("approve", "reject"):
            return {"error": f"Invalid decision: {decision!r}. Use 'approve' or 'reject'."}

        try:
            id_a = int(args["id_a"])
            id_b = int(args["id_b"])
        except (KeyError, TypeError, ValueError) as exc:
            return {"error": f"id_a / id_b must be integers: {exc}"}

        from goldenmatch.core.memory.store import Correction, _canon_pair

        ca, cb = _canon_pair(id_a, id_b)
        correction = Correction(
            id=str(uuid.uuid4()),
            id_a=ca,
            id_b=cb,
            decision=decision,
            source="agent",
            trust=0.5,
            field_hash="",
            record_hash="",
            original_score=0.0,
            matchkey_name=args.get("matchkey_name"),
            reason=args.get("reason"),
            dataset=dataset,
            created_at=datetime.now(),
        )
        with MemoryStore(backend="sqlite", path=path) as store:
            store.add_correction(correction)
        return {
            "status": "ok",
            "id": correction.id,
            "id_a": ca,
            "id_b": cb,
            "decision": decision,
            "source": "agent",
            "trust": 0.5,
            "dataset": dataset,
        }

    if name == "learn_thresholds":
        from goldenmatch.core.memory.learner import MemoryLearner

        matchkey_name = args.get("matchkey_name")
        with MemoryStore(backend="sqlite", path=path) as store:
            learner = MemoryLearner(store)
            adjustments = learner.learn(matchkey_name=matchkey_name)
        return {
            "count": len(adjustments),
            "adjustments": [_adjustment_to_dict(a) for a in adjustments],
        }

    if name == "memory_stats":
        with MemoryStore(backend="sqlite", path=path) as store:
            total = store.count_corrections()
            last = store.last_learn_time()
            adjustments = store.get_all_adjustments()
        return {
            "total_corrections": total,
            "last_learn_time": last.isoformat() if last else None,
            "adjustments": [_adjustment_to_dict(a) for a in adjustments],
        }

    if name == "memory_export":
        dataset = args.get("dataset")
        with MemoryStore(backend="sqlite", path=path) as store:
            corrections = store.get_corrections(dataset=dataset)
        return {
            "count": len(corrections),
            "corrections": [_correction_to_dict(c) for c in corrections],
        }

    return {"error": f"Unknown memory tool: {name}"}
