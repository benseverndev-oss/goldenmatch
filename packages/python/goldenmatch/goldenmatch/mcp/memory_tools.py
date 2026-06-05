"""MCP tool surface for Learning Memory."""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime
from typing import Any

from mcp.types import TextContent, Tool

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
            "Add a Learning Memory correction. Two shapes:\n"
            "  - pair-level: decision='approve' or 'reject', requires id_a + id_b\n"
            "  - field-level (v1.18.2+): decision='field_correct', requires "
            "cluster_id + field_name + corrected_value\n"
            "Source is 'agent' with trust=0.5 (lower than human steward 1.0). "
            "Pair (id_a, id_b) is canonicalized to (min, max) before storage."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "id_a": {
                    "type": "integer",
                    "description": "Pair-level: first row id. Field-level: ignored.",
                },
                "id_b": {
                    "type": "integer",
                    "description": "Pair-level: second row id. Field-level: ignored.",
                },
                "cluster_id": {
                    "type": "integer",
                    "description": "Field-level: cluster_id the correction targets.",
                },
                "decision": {
                    "type": "string",
                    "enum": ["approve", "reject", "field_correct"],
                },
                "field_name": {
                    "type": "string",
                    "description": "Field-level: the column being corrected.",
                },
                "original_value": {
                    "type": "string",
                    "description": "Field-level: the value build_golden_record chose.",
                },
                "corrected_value": {
                    "type": "string",
                    "description": "Field-level: the value the reviewer changed it to.",
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
            "required": ["decision", "dataset"],
        },
    ),
    Tool(
        name="list_plugins",
        description=(
            "List all registered goldenmatch plugins by category. Includes "
            "the 22 v1.18.2 predefined plugins (numeric/format/business/"
            "aggregation) plus any user-registered plugins via entry-points "
            "or PluginRegistry.register_*(). Each entry includes "
            "name, source (builtin or user), category, and the first line of "
            "the merge docstring."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": [
                        "all", "golden_strategy", "scorer", "transform", "connector",
                    ],
                    "default": "all",
                },
            },
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
    Tool(
        name="memory_import",
        description=(
            "Import corrections from a list of dicts (the exact shape "
            "memory_export returns). Upserts into the store: higher trust "
            "wins, same trust = latest wins. Returns the count imported."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "corrections": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Correction dicts, as returned by memory_export.",
                },
                "path": {
                    "type": "string",
                    "description": "SQLite memory DB path. Default: .goldenmatch/memory.db",
                },
            },
            "required": ["corrections"],
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


# Re-export so tests can monkeypatch the dispatcher's MemoryStore.
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
        dataset = args.get("dataset")
        if not dataset:
            return {"error": "Missing or empty required parameter: dataset"}

        decision = args.get("decision")
        if decision not in ("approve", "reject", "field_correct"):
            return {
                "error": f"Invalid decision: {decision!r}. Use 'approve', "
                         "'reject', or 'field_correct'.",
            }

        from goldenmatch.core.memory.store import Correction, _canon_pair

        # Field-level vs pair-level branching (Phase 1 of v1.18.3 surface sync).
        if decision == "field_correct":
            field_name = args.get("field_name")
            corrected_value = args.get("corrected_value")
            if not field_name:
                return {"error": "field_correct requires field_name"}
            if corrected_value is None:
                return {"error": "field_correct requires corrected_value"}
            # cluster_id occupies the id_a slot semantically; id_b=0 unused.
            try:
                cluster_id = int(args.get("cluster_id", args.get("id_a", 0)))
            except (TypeError, ValueError) as exc:
                return {"error": f"cluster_id must be an integer: {exc}"}
            correction = Correction(
                id=str(uuid.uuid4()),
                id_a=cluster_id,
                id_b=0,
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
                field_name=field_name,
                original_value=args.get("original_value"),
                corrected_value=corrected_value,
            )
            with MemoryStore(backend="sqlite", path=path) as store:
                store.add_correction(correction)
            return {
                "status": "ok",
                "id": correction.id,
                "cluster_id": cluster_id,
                "field_name": field_name,
                "original_value": args.get("original_value"),
                "corrected_value": corrected_value,
                "decision": decision,
                "source": "agent",
                "trust": 0.5,
                "dataset": dataset,
            }

        # Pair-level (approve / reject).
        try:
            id_a = int(args["id_a"])
            id_b = int(args["id_b"])
        except (KeyError, TypeError, ValueError) as exc:
            return {"error": f"id_a / id_b must be integers: {exc}"}
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

    if name == "list_plugins":
        from goldenmatch.plugins.builtin import BUILTIN_PLUGINS
        from goldenmatch.plugins.registry import PluginRegistry

        category = args.get("category", "all")
        registry = PluginRegistry.instance()
        registry.discover()
        builtin_names = {cls().name for cls in BUILTIN_PLUGINS}

        def _serialize(plugin_dict: dict, kind: str) -> list[dict]:
            out: list[dict] = []
            for plugin_name, plugin in plugin_dict.items():
                merge_doc = ""
                if hasattr(plugin, "merge") and plugin.merge.__doc__:
                    merge_doc = plugin.merge.__doc__.strip().split("\n")[0][:200]
                out.append({
                    "name": plugin_name,
                    "category": kind,
                    "source": "builtin" if (
                        kind == "golden_strategy" and plugin_name in builtin_names
                    ) else "user",
                    "doc": merge_doc,
                })
            return sorted(out, key=lambda d: (d["source"] != "builtin", d["name"]))

        result: dict[str, list[dict]] = {}
        kinds = (
            ("golden_strategy", "_golden_strategies"),
            ("scorer", "_scorers"),
            ("transform", "_transforms"),
            ("connector", "_connectors"),
        )
        for kind, attr in kinds:
            if category not in ("all", kind):
                continue
            store_dict = getattr(registry, attr, {})
            result[kind] = _serialize(store_dict, kind)
        return result

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

    if name == "memory_import":
        from goldenmatch.core.memory.store import Correction

        rows = args.get("corrections") or []
        imported = 0
        with MemoryStore(backend="sqlite", path=path) as store:
            for r in rows:
                created = r.get("created_at")
                store.add_correction(Correction(
                    id=r.get("id") or str(uuid.uuid4()),
                    id_a=int(r.get("id_a", 0)),
                    id_b=int(r.get("id_b", 0)),
                    decision=r["decision"],
                    source=r.get("source", "api"),
                    trust=float(r.get("trust", 0.5)),
                    field_hash=r.get("field_hash", ""),
                    record_hash=r.get("record_hash", ""),
                    original_score=float(r.get("original_score", 0.0)),
                    matchkey_name=r.get("matchkey_name"),
                    reason=r.get("reason"),
                    dataset=r.get("dataset"),
                    created_at=datetime.fromisoformat(created) if created else datetime.now(),
                ))
                imported += 1
        return {"imported": imported}

    return {"error": f"Unknown memory tool: {name}"}
