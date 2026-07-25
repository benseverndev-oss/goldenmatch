"""MCP tools for the Identity Graph.

- ``identity_resolve``   -> look up an identity by record_id
- ``identity_list``      -> list identities
- ``identity_history``   -> event log for an entity
- ``identity_conflicts`` -> conflicting evidence edges
- ``identity_merge``     -> manually merge two identities
- ``identity_split``     -> split records into a new identity
- ``identity_claim``     -> claim a record into an identity (move it)
- ``identity_resolve_conflict`` -> adjudicate a conflicts_with pair
- ``identity_audit``     -> export the append-only audit log (who/when/why)
- ``identity_audit_seal``   -> anchor the audit log with a tamper-evidence seal
- ``identity_audit_verify`` -> verify the audit log against its seal chain
- ``identity_show``      -> full detail of one identity
- ``identity_profile``   -> MDM profile of one entity (sources, conflicts, version)
- ``identity_stats``     -> graph-level summary / health stats
- ``identity_worklist``  -> prioritized steward worklist
"""
from __future__ import annotations

import json
import logging
from typing import Any

from mcp.types import TextContent, Tool

from goldenmatch.identity import (
    IdentityStore,
    audit_log_page,
    claim_record,
    entity_profile,
    find_by_record,
    find_conflicts,
    get_entity,
    history,
    identity_summary_stats,
    list_entities,
    manual_merge,
    manual_split,
    mediate_conflict,
    seal_audit_log,
    seal_result_dict,
    steward_worklist_page,
    verify_audit_chain,
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
            "`absorb_entity_id` are reassigned to `keep_entity_id`. The merge "
            "events are stamped with `actor`/`trust` provenance so the audit "
            "log records who merged these and on what authority."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "keep_entity_id": {"type": "string"},
                "absorb_entity_id": {"type": "string"},
                "reason": {"type": "string"},
                "actor": {
                    "type": "string",
                    "description": (
                        "Principal making the change, e.g. 'agent:claude' or "
                        "'steward:alice'. Defaults to 'agent'."
                    ),
                },
                "trust": {
                    "type": "number",
                    "description": (
                        "Trust of the actor in [0,1]. Defaults by actor prefix "
                        "(steward 1.0, agent 0.5)."
                    ),
                },
                "path": {"type": "string"},
            },
            "required": ["keep_entity_id", "absorb_entity_id"],
        },
    ),
    Tool(
        name="identity_split",
        description=(
            "Split a subset of records off an identity into a brand-new "
            "identity. The original keeps the remaining records. The split "
            "events carry `actor`/`trust` provenance."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "entity_id": {"type": "string"},
                "record_ids": {"type": "array", "items": {"type": "string"}},
                "reason": {"type": "string"},
                "actor": {
                    "type": "string",
                    "description": (
                        "Principal making the change, e.g. 'agent:claude'. "
                        "Defaults to 'agent'."
                    ),
                },
                "trust": {
                    "type": "number",
                    "description": "Trust of the actor in [0,1]. Default by actor prefix.",
                },
                "path": {"type": "string"},
            },
            "required": ["entity_id", "record_ids"],
        },
    ),
    Tool(
        name="identity_claim",
        description=(
            "Claim a record into an identity, moving it out of any prior "
            "entity ('this record belongs to that identity'). Emits a "
            "provenance-stamped `claimed` event on both the gaining and losing "
            "entities."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "entity_id": {"type": "string", "description": "Entity to claim the record into"},
                "record_id": {"type": "string", "description": "record id in `{source}:{source_pk}` form"},
                "reason": {"type": "string"},
                "actor": {
                    "type": "string",
                    "description": "Principal, e.g. 'agent:claude'. Defaults to 'agent'.",
                },
                "trust": {"type": "number", "description": "Trust in [0,1]. Default by actor prefix."},
                "path": {"type": "string"},
            },
            "required": ["entity_id", "record_id"],
        },
    ),
    Tool(
        name="identity_resolve_conflict",
        description=(
            "Adjudicate a `conflicts_with` pair: 'same' keeps the entity "
            "intact, 'distinct' splits the second record out into a new "
            "identity, 'defer' only logs. Records a durable mediation verdict "
            "+ event with actor/trust provenance, and stops the conflict "
            "re-surfacing in the open-conflicts queue."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "record_a_id": {"type": "string"},
                "record_b_id": {"type": "string"},
                "resolution": {
                    "type": "string",
                    "enum": ["same", "distinct", "defer"],
                },
                "reason": {"type": "string"},
                "dataset": {"type": "string"},
                "apply": {
                    "type": "boolean",
                    "default": True,
                    "description": "Act on the verdict (split on 'distinct'); false = log only.",
                },
                "actor": {
                    "type": "string",
                    "description": "Principal, e.g. 'steward:alice'. Defaults to 'agent'.",
                },
                "trust": {"type": "number", "description": "Trust in [0,1]. Default by actor prefix."},
                "path": {"type": "string"},
            },
            "required": ["record_a_id", "record_b_id", "resolution"],
        },
    ),
    Tool(
        name="identity_audit",
        description=(
            "Export the append-only identity audit log in commit order: every "
            "event with actor / trust / timestamp / reason, so a reviewer can "
            "reconstruct exactly which actor changed what, when, and why. "
            "Optionally filtered by dataset / actor."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "dataset": {"type": "string"},
                "actor": {"type": "string"},
                "limit": {"type": "integer", "default": 500},
                "path": {"type": "string"},
            },
        },
    ),
    Tool(
        name="identity_audit_seal",
        description=(
            "Anchor the append-only audit log with a tamper-evidence seal: a "
            "chained sha256 root over every event since the last seal. Cheap "
            "and idempotent (a no-op when nothing new has been logged). Run it "
            "periodically (or after a batch of stewardship actions) so the "
            "history becomes provably untampered. Optionally scoped to a "
            "dataset. Publish/mirror the returned root_hash to make tampering "
            "detectable by an external party."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "dataset": {"type": "string"},
                "actor": {
                    "type": "string",
                    "description": "Principal sealing the log. Defaults to 'agent'.",
                },
                "path": {"type": "string", "description": "Identity DB path"},
            },
        },
    ),
    Tool(
        name="identity_audit_verify",
        description=(
            "Verify the append-only audit log against its seal chain. Replays "
            "the per-event content hashes and the seal roots to detect content "
            "edits, deletion, reordering, and insertion of any sealed event. "
            "Returns {ok, events_checked, seals_checked} plus the ids of any "
            "content mismatches / broken seals / missing sealed events. "
            "Optionally scoped to a dataset."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "dataset": {"type": "string"},
                "path": {"type": "string", "description": "Identity DB path"},
            },
        },
    ),
    Tool(
        name="identity_show",
        description=(
            "Fetch the full detail of one identity by entity_id: its member "
            "records, evidence edges, and recent event log. Returns "
            "{found: false} when no such entity exists."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "entity_id": {"type": "string"},
                "event_limit": {"type": "integer", "default": 100},
                "path": {"type": "string", "description": "Identity DB path"},
            },
            "required": ["entity_id"],
        },
    ),
    Tool(
        name="identity_profile",
        description=(
            "MDM profile of one entity: record count + per-source breakdown, "
            "golden record, confidence, conflict count, canonical version "
            "(structural-event count), and first/last activity. "
            "Returns {found: false} when no such entity exists."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "entity_id": {"type": "string"},
                "path": {"type": "string", "description": "Identity DB path"},
            },
            "required": ["entity_id"],
        },
    ),
    Tool(
        name="identity_stats",
        description=(
            "Graph-level summary / health stats: entities by status, total "
            "records, records-per-entity distribution, conflict total, source "
            "mix, and the largest entities. Optionally scoped to a dataset."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "dataset": {"type": "string"},
                "path": {"type": "string", "description": "Identity DB path"},
            },
        },
    ),
    Tool(
        name="identity_worklist",
        description=(
            "Prioritized steward worklist: active entities needing attention "
            "(open conflicts and/or confidence below weak_confidence), highest "
            "conflict count first."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "dataset": {"type": "string"},
                "weak_confidence": {"type": "number", "default": 0.6},
                "limit": {"type": "integer", "default": 50},
                "path": {"type": "string", "description": "Identity DB path"},
            },
        },
    ),
]


IDENTITY_TOOL_NAMES = frozenset(t.name for t in IDENTITY_TOOLS)


def _open(args: dict) -> IdentityStore:
    return IdentityStore(path=args.get("path") or _DEFAULT_PATH)


def _actor_trust(args: dict) -> tuple[str, float | None]:
    """Resolve the (actor, trust) provenance for an agent-driven mutation.

    ``actor`` defaults to ``"agent"`` (MCP is the agent surface). When ``trust``
    is not supplied, it's derived from the actor's prefix
    (``steward:`` -> 1.0, else 0.5) via the shared trust map, so an agent write
    is recorded at lower authority than a steward's."""
    actor = str(args.get("actor") or "agent")
    trust = args.get("trust")
    if trust is None:
        try:
            from goldenmatch.core.memory.store import trust_for_source
            trust = trust_for_source(actor.split(":", 1)[0])
        except Exception:
            trust = None
    return actor, (float(trust) if trust is not None else None)


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
        actor, trust = _actor_trust(args)
        with _open(args) as s:
            return manual_merge(
                s,
                keep_entity_id=args["keep_entity_id"],
                absorb_entity_id=args["absorb_entity_id"],
                reason=args.get("reason"),
                run_name="mcp",
                actor=actor,
                trust=trust,
            )

    if name == "identity_split":
        actor, trust = _actor_trust(args)
        with _open(args) as s:
            return manual_split(
                s,
                entity_id=args["entity_id"],
                record_ids=list(args["record_ids"]),
                reason=args.get("reason"),
                run_name="mcp",
                actor=actor,
                trust=trust,
            )

    if name == "identity_claim":
        actor, trust = _actor_trust(args)
        with _open(args) as s:
            return claim_record(
                s,
                entity_id=args["entity_id"],
                record_id=args["record_id"],
                reason=args.get("reason"),
                run_name="mcp",
                actor=actor,
                trust=trust,
            )

    if name == "identity_resolve_conflict":
        actor, trust = _actor_trust(args)
        with _open(args) as s:
            return mediate_conflict(
                s,
                args["record_a_id"],
                args["record_b_id"],
                args["resolution"],
                reason=args.get("reason"),
                dataset=args.get("dataset"),
                apply=bool(args.get("apply", True)),
                actor=actor,
                trust=trust,
            )

    if name == "identity_audit":
        limit = int(args.get("limit", 500))
        with _open(args) as s:
            return audit_log_page(
                s, dataset=args.get("dataset"), actor=args.get("actor"), limit=limit
            )

    if name == "identity_audit_seal":
        actor, _ = _actor_trust(args)
        with _open(args) as s:
            return seal_result_dict(
                seal_audit_log(s, actor=actor, dataset=args.get("dataset"))
            )

    if name == "identity_audit_verify":
        with _open(args) as s:
            return verify_audit_chain(s, dataset=args.get("dataset")).as_dict()

    if name == "identity_show":
        with _open(args) as s:
            view = get_entity(s, args["entity_id"], event_limit=int(args.get("event_limit", 100)))
        return view.to_dict() if view else {"found": False}

    if name == "identity_profile":
        with _open(args) as s:
            prof = entity_profile(s, args["entity_id"])
        return prof.as_dict() if prof else {"found": False}

    if name == "identity_stats":
        with _open(args) as s:
            return identity_summary_stats(s, dataset=args.get("dataset")).as_dict()

    if name == "identity_worklist":
        with _open(args) as s:
            return steward_worklist_page(
                s,
                dataset=args.get("dataset"),
                weak_confidence=float(args.get("weak_confidence", 0.6)),
                limit=int(args.get("limit", 50)),
            )

    raise ValueError(f"unknown identity tool: {name}")


async def handle_identity_tool(name: str, args: dict) -> list[TextContent]:
    """Async wrapper for direct MCP server registration."""
    payload = _dispatch(name, args)
    return [TextContent(type="text", text=json.dumps(payload, default=str))]
