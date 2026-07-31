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
- ``customer_360``       -> the unified serving view of one entity (golden record +
                            provenance + linked records + timeline + relationships)
- ``certify_serving_joins`` -> certify that a Customer 360 serving layer's
                            source-record join key is unique (can't double-count)
- ``emit_semantic_model_from_store`` -> emit a conformed semantic-layer catalog
                            (the ``resolved_entity_id`` join) directly from the store
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
    customer_360_page,
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
    Tool(
        name="customer_360",
        description=(
            "The unified Customer 360 serving view of one entity, composed from "
            "the durable store in one call: the golden record + per-field source "
            "provenance, every linked source record, the event timeline, and the "
            "relationship neighborhood. This is the same durable entity_id a "
            "resolved crosswalk (semantic-layer wedge) groups metrics by, so a "
            "metric row drills straight through to the customer. Returns "
            "{found: false} when no such entity exists."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "entity_id": {"type": "string"},
                "include_relationships": {"type": "boolean", "default": True},
                "timeline_limit": {
                    "type": "integer",
                    "description": "Cap the number of timeline events (most recent first).",
                },
                "path": {"type": "string", "description": "Identity DB path"},
            },
            "required": ["entity_id"],
        },
    ),
    Tool(
        name="certify_serving_joins",
        description=(
            "Certify that a Customer 360 serving layer's join keys can't "
            "double-count. A 360 view joins the golden record to its source "
            "records on the durable record_id (`{source}:{source_pk}`); if that "
            "key is duplicated, a fact rolled up through the 360 silently "
            "double-counts. This walks the store's entities, assembles the "
            "record_id join key, and returns a key-integrity certificate over it "
            "({trustworthy, n_entities, n_records, truncated, record_id: "
            "{is_unique_at_grain, duplicate_key_groups, max_fan_out, estimate}})."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "dataset": {"type": "string", "description": "Restrict to one identity-graph dataset."},
                "status": {
                    "type": "string",
                    "default": "active",
                    "description": "Entity status to include (default 'active').",
                },
                "page_size": {"type": "integer", "default": 500, "description": "Entity-scan pagination size."},
                "max_entities": {
                    "type": "integer",
                    "description": "Cap the scan at this many entities (cert then covers a prefix, truncated=true).",
                },
                "path": {"type": "string", "description": "Identity DB path"},
            },
        },
    ),
    Tool(
        name="emit_semantic_model_from_store",
        description=(
            "Emit a conformed semantic-layer catalog (the `resolved_entity_id` "
            "join a MetricFlow / Cube / OSI model should group metrics by) "
            "directly from the durable identity store — 'keep the semantic "
            "layer's identity join live against the control plane'. Returns the "
            "emitted YAML; when `path` is set, also writes it to that catalog "
            "file (refuses to clobber unless overwrite=true)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "source_name": {
                    "type": "string",
                    "description": "Logical source name the records were ingested under.",
                },
                "source_pk_column": {
                    "type": "string",
                    "description": "Column holding each record's source primary key (the join column).",
                },
                "dialect": {
                    "type": "string",
                    "enum": ["metricflow", "cube", "osi"],
                    "default": "metricflow",
                },
                "dataset": {"type": "string", "description": "Identity-graph dataset scope (defaults to all)."},
                "source_target": {
                    "type": "string",
                    "description": "Source model / cube / dataset the join points at (defaults to source_name).",
                },
                "resolved_key": {
                    "type": "string",
                    "default": "resolved_entity_id",
                    "description": "The conformed join column name (the control-plane id).",
                },
                "out_path": {
                    "type": "string",
                    "description": "Optional catalog file to write the emitted YAML to.",
                },
                "overwrite": {
                    "type": "boolean",
                    "default": False,
                    "description": "Overwrite an existing catalog file at `out_path`.",
                },
                "path": {"type": "string", "description": "Identity DB path"},
            },
            "required": ["source_name", "source_pk_column"],
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


def _serving_certificate_dict(cert: Any) -> dict[str, Any]:
    """Serialize a `ServingJoinCertificate` (from `certify_serving_joins`) to a
    JSON-safe dict, projecting the inner `KeyIntegrityCertificate`."""
    rc = cert.record_certificate
    return {
        "trustworthy": cert.is_trustworthy,
        "n_entities": cert.n_entities,
        "n_records": cert.n_records,
        "truncated": cert.truncated,
        "record_id": {
            "is_unique_at_grain": rc.is_unique_at_grain,
            "duplicate_key_groups": rc.duplicate_key_groups,
            "max_fan_out": rc.max_fan_out,
            "estimate": rc.estimate,
        },
    }


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

    if name == "customer_360":
        tl = args.get("timeline_limit")
        with _open(args) as s:
            page = customer_360_page(
                s,
                args["entity_id"],
                include_relationships=bool(args.get("include_relationships", True)),
                timeline_limit=int(tl) if tl is not None else None,
            )
        return page if page is not None else {"found": False}

    if name == "certify_serving_joins":
        from goldenmatch.semantic import certify_serving_joins
        me = args.get("max_entities")
        with _open(args) as s:
            cert = certify_serving_joins(
                s,
                dataset=args.get("dataset"),
                status=args.get("status", "active"),
                page_size=int(args.get("page_size", 500)),
                max_entities=int(me) if me is not None else None,
            )
        return _serving_certificate_dict(cert)

    if name == "emit_semantic_model_from_store":
        from goldenmatch.semantic import emit_semantic_model_from_store
        emit_kwargs = {
            k: args[k]
            for k in ("dataset", "source_target")
            if args.get(k) is not None
        }
        with _open(args) as s:
            yaml_str = emit_semantic_model_from_store(
                s,
                source_name=args["source_name"],
                source_pk_column=args["source_pk_column"],
                dialect=args.get("dialect", "metricflow"),
                resolved_key=args.get("resolved_key", "resolved_entity_id"),
                path=args.get("out_path"),
                overwrite=bool(args.get("overwrite", False)),
                **emit_kwargs,
            )
        return {"yaml": yaml_str, "written_to": args.get("out_path")}

    raise ValueError(f"unknown identity tool: {name}")


async def handle_identity_tool(name: str, args: dict) -> list[TextContent]:
    """Async wrapper for direct MCP server registration."""
    payload = _dispatch(name, args)
    return [TextContent(type="text", text=json.dumps(payload, default=str))]
