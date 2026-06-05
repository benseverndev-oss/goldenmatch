"""Agent-level MCP tools for autonomous entity resolution.

Each tool creates its own AgentSession (no shared global state),
delegates to the appropriate AgentSession method, and returns
results as JSON in TextContent.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from mcp.types import TextContent, Tool

from goldenmatch._exclusions_schema import (
    EXCLUDE_COLUMNS_SCHEMA as _EXCLUDE_COLUMNS_SCHEMA,
)
from goldenmatch.core._logging import sanitize_for_log

if TYPE_CHECKING:
    from goldenmatch.core.memory.store import MemoryStore

logger = logging.getLogger(__name__)


def _write_agent_correction(
    *,
    memory_store: MemoryStore,
    session: Any,
    id_a: int,
    id_b: int,
    decision: str,
    reason: str | None,
    dataset: str | None,
) -> None:
    """Write a Correction with source='agent', trust=0.5.

    Hashes are computed from `session.data` when present; otherwise empty.
    """
    try:
        import uuid
        from datetime import datetime

        from goldenmatch.core.memory.store import Correction, _canon_pair

        ca, cb = _canon_pair(id_a, id_b)
        field_hash = ""
        record_hash = ""
        df = getattr(session, "data", None)
        if df is not None:
            try:
                from goldenmatch.core.memory.corrections import (
                    build_row_lookup,
                    compute_field_hash,
                    compute_record_hash,
                )

                cols = [c for c in df.columns if not c.startswith("__")]
                if cols:
                    lookup = build_row_lookup(df, cols)
                    if ca in lookup and cb in lookup:
                        field_hash = compute_field_hash(lookup[ca], lookup[cb])
                ra = compute_record_hash(df, ca)
                rb = compute_record_hash(df, cb)
                if ra and rb:
                    record_hash = f"{ra}:{rb}"
            except Exception as e:
                logger.warning(
                    "agent correction hash computation failed for pair (%s,%s); "
                    "writing empty hashes - staleness detection degraded: %s",
                    ca, cb, e,
                )
                field_hash, record_hash = "", ""

        memory_store.add_correction(Correction(
            id=str(uuid.uuid4()),
            id_a=id_a,
            id_b=id_b,
            decision=decision,
            source="agent",
            trust=0.5,
            field_hash=field_hash,
            record_hash=record_hash,
            original_score=0.0,
            matchkey_name=None,
            reason=reason,
            dataset=dataset,
            created_at=datetime.now(),
        ))
    except Exception as e:
        logger.warning("agent_approve_reject memory write failed: %s", e)


AGENT_TOOLS = [
    Tool(
        name="analyze_data",
        description="Profile data, detect domain, recommend ER strategy",
        inputSchema={
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
            },
            "required": ["file_path"],
        },
    ),
    Tool(
        name="auto_configure",
        description=(
            "Run AutoConfigController on a CSV; return the committed "
            "GoldenMatchConfig (incl. negative_evidence / Path Y when chosen) "
            "plus telemetry — stop_reason, health, decision trace, indicator "
            "column priors. Programmatic equivalent of `goldenmatch autoconfig`."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "constraints": {"type": "object"},
                "exclude_columns": _EXCLUDE_COLUMNS_SCHEMA,
            },
            "required": ["file_path"],
        },
    ),
    Tool(
        name="controller_telemetry",
        description=(
            "Return the AutoConfigController telemetry from the most recent "
            "`auto_configure` or `agent_deduplicate` call in this MCP session. "
            "Same JSON shape as the web /api/v1/controller/telemetry endpoint."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
    Tool(
        name="agent_deduplicate",
        description="Run full ER pipeline with confidence gating and reasoning",
        inputSchema={
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "config": {"type": "object"},
                "exclude_columns": _EXCLUDE_COLUMNS_SCHEMA,
            },
            "required": ["file_path"],
        },
    ),
    Tool(
        name="agent_match_sources",
        description="Match two files with intelligent strategy selection",
        inputSchema={
            "type": "object",
            "properties": {
                "file_a": {"type": "string"},
                "file_b": {"type": "string"},
                "config": {"type": "object"},
                "exclude_columns": _EXCLUDE_COLUMNS_SCHEMA,
            },
            "required": ["file_a", "file_b"],
        },
    ),
    Tool(
        name="agent_explain_pair",
        description="Natural language explanation for a record pair",
        inputSchema={
            "type": "object",
            "properties": {
                "record_a": {"type": "object"},
                "record_b": {"type": "object"},
                "fuzzy": {"type": "object"},
                "exact": {"type": "array"},
            },
            "required": ["record_a", "record_b"],
        },
    ),
    Tool(
        name="agent_explain_cluster",
        description="Explain why records are in the same cluster",
        inputSchema={
            "type": "object",
            "properties": {
                "cluster_id": {"type": "integer"},
            },
            "required": ["cluster_id"],
        },
    ),
    Tool(
        name="agent_review_queue",
        description="Get borderline pairs awaiting approval",
        inputSchema={
            "type": "object",
            "properties": {
                "job_name": {"type": "string"},
            },
            "required": ["job_name"],
        },
    ),
    Tool(
        name="agent_approve_reject",
        description="Approve or reject a review queue pair",
        inputSchema={
            "type": "object",
            "properties": {
                "job_name": {"type": "string"},
                "id_a": {"type": "integer"},
                "id_b": {"type": "integer"},
                "decision": {"type": "string"},
                "decided_by": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["job_name", "id_a", "id_b", "decision", "decided_by"],
        },
    ),
    Tool(
        name="agent_compare_strategies",
        description="Compare ER strategies on your data",
        inputSchema={
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "ground_truth": {"type": "string"},
            },
            "required": ["file_path"],
        },
    ),
    Tool(
        name="suggest_pprl",
        description="Check if data needs privacy-preserving matching",
        inputSchema={
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
            },
            "required": ["file_path"],
        },
    ),
    Tool(
        name="scan_quality",
        description=(
            "Run GoldenCheck data quality scan on a CSV file. "
            "Returns issues found (encoding errors, Unicode problems, format violations) "
            "without applying fixes. Requires goldencheck: pip install goldenmatch[quality]"
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the CSV file to scan",
                },
                "domain": {
                    "type": "string",
                    "description": "Optional domain hint (healthcare, finance, ecommerce)",
                },
            },
            "required": ["file_path"],
        },
    ),
    Tool(
        name="fix_quality",
        description=(
            "Run GoldenCheck scan and apply fixes to a CSV file. "
            "Returns the fixed data summary and a manifest of all fixes applied. "
            "Requires goldencheck: pip install goldenmatch[quality]"
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the CSV file to fix",
                },
                "fix_mode": {
                    "type": "string",
                    "enum": ["safe", "moderate"],
                    "description": "Fix aggressiveness: safe (conservative) or moderate (balanced). Default: safe",
                    "default": "safe",
                },
                "domain": {
                    "type": "string",
                    "description": "Optional domain hint (healthcare, finance, ecommerce)",
                },
                "output_path": {
                    "type": "string",
                    "description": "Optional path to save the fixed CSV. If omitted, returns summary only.",
                },
            },
            "required": ["file_path"],
        },
    ),
    Tool(
        name="run_transforms",
        description=(
            "Run GoldenFlow data transforms on a CSV file. "
            "Normalizes phone numbers (E.164), dates (ISO), categorical spelling, "
            "and Unicode issues. Returns a manifest of transforms applied. "
            "Requires goldenflow: pip install goldenmatch[transform]"
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the CSV file to transform",
                },
                "output_path": {
                    "type": "string",
                    "description": "Optional path to save the transformed CSV. If omitted, returns summary only.",
                },
            },
            "required": ["file_path"],
        },
    ),
    Tool(
        name="sensitivity",
        description=(
            "Parameter-sensitivity analysis: sweep one or more config "
            "parameters across a range and report how stable the clustering "
            "is at each value (CCMS unchanged %). Use it to find robust "
            "thresholds. Auto-configures the file if no config is given."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "CSV/Parquet to analyze"},
                "sweep": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Sweep specs as 'field:start:stop:step', e.g. "
                        "'threshold:0.70:0.95:0.05'. One or more."
                    ),
                },
                "config": {"type": "string", "description": "Optional config YAML path"},
                "sample_size": {
                    "type": "integer",
                    "description": "Optional: randomly sample N records before sweeping",
                },
            },
            "required": ["file_path", "sweep"],
        },
    ),
    Tool(
        name="incremental",
        description=(
            "Match a batch of new records against an existing base dataset "
            "(without re-running the whole base). Returns matched "
            "(new_row_id, base_row_id, score) pairs plus counts. "
            "Auto-configures from the base file if no config is given."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "base_file": {"type": "string", "description": "Existing base dataset path"},
                "new_records": {"type": "string", "description": "New records file to match in"},
                "config": {"type": "string", "description": "Optional config YAML path"},
                "threshold": {"type": "number", "description": "Optional threshold override"},
            },
            "required": ["base_file", "new_records"],
        },
    ),
]

_AGENT_TOOL_NAMES = frozenset(t.name for t in AGENT_TOOLS)


def _serialize_result(result: Any) -> dict:
    """Convert pipeline result objects to JSON-safe dicts."""
    if hasattr(result, "clusters") and hasattr(result, "stats"):
        # DedupeResult / MatchResult
        clusters = result.clusters or {}
        multi = sum(1 for c in clusters.values() if c.get("size", 0) > 1)
        total_matched = sum(
            c.get("size", 0) for c in clusters.values() if c.get("size", 0) > 1
        )
        stats = result.stats if isinstance(result.stats, dict) else {}
        return {
            "total_records": stats.get("total_records", 0),
            "total_clusters": multi,
            "total_matched_records": total_matched,
            "match_rate": stats.get("match_rate", 0.0),
            "scored_pairs": len(result.scored_pairs) if result.scored_pairs else 0,
        }
    if isinstance(result, dict):
        return result
    return {"value": str(result)}


def handle_agent_tool(name: str, arguments: dict) -> list[TextContent]:
    """Route an agent-level MCP tool call to the appropriate handler.

    Creates a fresh AgentSession per call (stateless).
    Returns results as JSON in TextContent.
    """
    from goldenmatch.core.agent import AgentSession

    try:
        result = _dispatch(name, arguments, AgentSession)
        return [TextContent(
            type="text",
            text=json.dumps(result, default=str, indent=2),
        )]
    except Exception as exc:
        logger.exception("Agent tool %s failed", name)
        return [TextContent(
            type="text",
            text=json.dumps({"error": str(exc)}),
        )]


def _dispatch(
    name: str,
    args: dict,
    session_cls: type,
    *,
    memory_store: MemoryStore | None = None,
    dataset: str | None = None,
) -> dict:
    """Dispatch to the appropriate handler by tool name."""

    if name == "analyze_data":
        session = session_cls()
        return session.analyze(args["file_path"])

    if name == "auto_configure":
        # v1.7-v1.12: route through AgentSession.autoconfigure which calls
        # auto_configure_df and captures controller telemetry. The legacy
        # `select_strategy` heuristic path is still reachable via
        # `analyze_data` if a caller wants the lighter profile-only view.
        session = session_cls()
        excl = args.get("exclude_columns") or None
        _excl_token = None
        if excl:
            from goldenmatch.core.autoconfig import _RUNTIME_EXCLUDE_COLUMNS
            _excl_token = _RUNTIME_EXCLUDE_COLUMNS.set(list(excl))
        try:
            return session.autoconfigure(args["file_path"])
        finally:
            if _excl_token is not None:
                from goldenmatch.core.autoconfig import _RUNTIME_EXCLUDE_COLUMNS
                _RUNTIME_EXCLUDE_COLUMNS.reset(_excl_token)

    if name == "controller_telemetry":
        # Stateless MCP — each tool call instantiates a fresh AgentSession,
        # so we can't read telemetry from a prior call. Surface that clearly
        # rather than silently returning the unavailable sentinel.
        return {
            "available": False,
            "note": (
                "controller_telemetry is per-session, but MCP tool calls are "
                "stateless. Call auto_configure or agent_deduplicate in the "
                "same tool invocation chain to get telemetry alongside the "
                "result; that tool already returns telemetry inline."
            ),
        }

    if name == "agent_deduplicate":
        session = session_cls()
        config_arg = args.get("config")
        excl = args.get("exclude_columns") or None
        _excl_token = None
        if excl:
            from goldenmatch.core.autoconfig import _RUNTIME_EXCLUDE_COLUMNS
            _excl_token = _RUNTIME_EXCLUDE_COLUMNS.set(list(excl))
        try:
            raw = session.deduplicate(args["file_path"], config=config_arg)
        finally:
            if _excl_token is not None:
                from goldenmatch.core.autoconfig import _RUNTIME_EXCLUDE_COLUMNS
                _RUNTIME_EXCLUDE_COLUMNS.reset(_excl_token)
        return {
            "reasoning": raw.get("reasoning", {}),
            "confidence_distribution": raw.get("confidence_distribution", {}),
            "storage": raw.get("storage", "memory"),
            "telemetry": session.last_telemetry
                or {"available": False, "source": None},
            "results": _serialize_result(raw.get("results")),
        }

    if name == "agent_match_sources":
        session = session_cls()
        config_arg = args.get("config")
        excl = args.get("exclude_columns") or None
        _excl_token = None
        if excl:
            from goldenmatch.core.autoconfig import _RUNTIME_EXCLUDE_COLUMNS
            _excl_token = _RUNTIME_EXCLUDE_COLUMNS.set(list(excl))
        try:
            raw = session.match_sources(args["file_a"], args["file_b"], config=config_arg)
        finally:
            if _excl_token is not None:
                from goldenmatch.core.autoconfig import _RUNTIME_EXCLUDE_COLUMNS
                _RUNTIME_EXCLUDE_COLUMNS.reset(_excl_token)
        return {
            "reasoning": raw.get("reasoning", {}),
            "telemetry": session.last_telemetry
                or {"available": False, "source": None},
            "results": _serialize_result(raw.get("results")),
        }

    if name == "agent_explain_pair":
        from goldenmatch._api import explain_pair_df
        fuzzy = args.get("fuzzy")
        exact = args.get("exact")
        explanation = explain_pair_df(
            args["record_a"],
            args["record_b"],
            fuzzy=fuzzy,
            exact=exact,
        )
        return {"explanation": explanation}

    if name == "agent_explain_cluster":
        cluster_id = args["cluster_id"]
        # With no global state, return a descriptive message
        return {
            "cluster_id": cluster_id,
            "note": (
                "agent_explain_cluster requires a prior agent_deduplicate call. "
                "Each MCP tool call is stateless; run agent_deduplicate first, "
                "then inspect the clusters dict directly."
            ),
        }

    if name == "agent_review_queue":
        session = session_cls()
        job_name = args["job_name"]
        pending = session.review_queue.list_pending(job_name)
        return {
            "job_name": job_name,
            "pending": [
                {
                    "id_a": item.id_a,
                    "id_b": item.id_b,
                    "score": item.score,
                    "explanation": item.explanation,
                }
                for item in pending
            ],
            "count": len(pending),
        }

    if name == "agent_approve_reject":
        session = session_cls()
        job_name = args["job_name"]
        decision = args["decision"]
        decided_by = args["decided_by"]
        reason = args.get("reason", "")

        if decision == "approve":
            session.review_queue.approve(
                job_name, args["id_a"], args["id_b"], decided_by,
            )
        elif decision == "reject":
            session.review_queue.reject(
                job_name, args["id_a"], args["id_b"], decided_by, reason,
            )
        else:
            return {"error": f"Invalid decision: {decision!r}. Use 'approve' or 'reject'."}

        if memory_store is not None:
            _write_agent_correction(
                memory_store=memory_store,
                session=session,
                id_a=int(args["id_a"]),
                id_b=int(args["id_b"]),
                decision=decision,
                reason=reason or None,
                dataset=dataset,
            )

        return {
            "status": "ok",
            "decision": decision,
            "job_name": job_name,
            "id_a": args["id_a"],
            "id_b": args["id_b"],
            "decided_by": decided_by,
        }

    if name == "agent_compare_strategies":
        session = session_cls()
        ground_truth = args.get("ground_truth")
        return session.compare_strategies(args["file_path"], ground_truth)

    if name == "suggest_pprl":
        session = session_cls()
        analysis = session.analyze(args["file_path"])
        needs_pprl = analysis.get("strategy") == "pprl"
        return {
            "needs_pprl": needs_pprl,
            "strategy": analysis.get("strategy"),
            "why": analysis.get("why"),
            "has_sensitive": analysis.get("profile", {}).get("has_sensitive", False),
            "recommendation": (
                "Use PPRL (privacy-preserving record linkage) for this data."
                if needs_pprl
                else "Standard matching is safe for this data. PPRL is optional."
            ),
        }

    if name == "scan_quality":
        import polars as pl

        from goldenmatch.config.schemas import QualityConfig
        from goldenmatch.core.quality import _goldencheck_available, run_quality_check

        if not _goldencheck_available():
            return {
                "error": "goldencheck is not installed. Install with: pip install goldenmatch[quality]",
            }

        file_path = args.get("file_path")
        if not file_path:
            return {"error": "Missing required parameter: file_path"}

        try:
            df = pl.read_csv(file_path, encoding="utf8-lossy", ignore_errors=True)
        except FileNotFoundError:
            return {"error": f"File not found: {file_path}"}
        except Exception as exc:
            return {"error": f"Could not read CSV '{file_path}': {exc}"}

        logger.info("scan_quality: scanning %s (%d records)", sanitize_for_log(file_path), df.height)
        qc = QualityConfig(mode="silent", fix_mode="none", domain=args.get("domain"))
        _, issues = run_quality_check(df, qc)
        logger.info("scan_quality: found %d issues", len(issues))

        return {
            "file": file_path,
            "total_records": df.height,
            "issues_found": len(issues),
            "issues": issues,
        }

    if name == "fix_quality":
        import polars as pl

        from goldenmatch.config.schemas import QualityConfig
        from goldenmatch.core.quality import _goldencheck_available, run_quality_check

        if not _goldencheck_available():
            return {
                "error": "goldencheck is not installed. Install with: pip install goldenmatch[quality]",
            }

        file_path = args.get("file_path")
        if not file_path:
            return {"error": "Missing required parameter: file_path"}

        try:
            df = pl.read_csv(file_path, encoding="utf8-lossy", ignore_errors=True)
        except FileNotFoundError:
            return {"error": f"File not found: {file_path}"}
        except Exception as exc:
            return {"error": f"Could not read CSV '{file_path}': {exc}"}

        fix_mode = args.get("fix_mode", "safe")
        domain = args.get("domain")
        logger.info("fix_quality: fixing %s (mode=%s)", sanitize_for_log(file_path), sanitize_for_log(fix_mode))
        qc = QualityConfig(mode="silent", fix_mode=fix_mode, domain=domain)
        fixed_df, fixes = run_quality_check(df, qc)
        logger.info("fix_quality: %d fixes applied", len(fixes))

        output_path = args.get("output_path")
        write_error = None
        if output_path:
            try:
                fixed_df.write_csv(output_path)
            except Exception as exc:
                write_error = f"Results computed but failed to write to '{output_path}': {exc}"
                output_path = None

        result = {
            "file": file_path,
            "fix_mode": fix_mode,
            "total_records": fixed_df.height,
            "fixes_applied": len(fixes),
            "fixes": fixes,
            "output_path": output_path,
        }
        if write_error:
            result["write_error"] = write_error
        return result

    if name == "run_transforms":
        import polars as pl

        from goldenmatch.config.schemas import TransformConfig
        from goldenmatch.core.transform import _goldenflow_available, run_transform

        if not _goldenflow_available():
            return {
                "error": "goldenflow is not installed. Install with: pip install goldenmatch[transform]",
            }

        file_path = args.get("file_path")
        if not file_path:
            return {"error": "Missing required parameter: file_path"}

        try:
            df = pl.read_csv(file_path, encoding="utf8-lossy", ignore_errors=True)
        except FileNotFoundError:
            return {"error": f"File not found: {file_path}"}
        except Exception as exc:
            return {"error": f"Could not read CSV '{file_path}': {exc}"}

        logger.info("run_transforms: transforming %s (%d records)", sanitize_for_log(file_path), df.height)
        tc = TransformConfig(mode="silent")
        transformed_df, fixes = run_transform(df, tc, strict=True)
        logger.info("run_transforms: %d transforms applied", len(fixes))

        output_path = args.get("output_path")
        write_error = None
        if output_path:
            try:
                transformed_df.write_csv(output_path)
            except Exception as exc:
                write_error = f"Results computed but failed to write to '{output_path}': {exc}"
                output_path = None

        result = {
            "file": file_path,
            "total_records": transformed_df.height,
            "transforms_applied": len(fixes),
            "transforms": fixes,
            "output_path": output_path,
        }
        if write_error:
            result["write_error"] = write_error
        return result

    if name == "sensitivity":
        from pathlib import Path as _Path

        from goldenmatch.core.sensitivity import SweepParam, run_sensitivity

        file_path = args["file_path"]
        specs = [(file_path, _Path(file_path).stem)]
        cfg_path = args.get("config")
        if cfg_path:
            from goldenmatch.config.loader import load_config
            cfg = load_config(cfg_path)
        else:
            from goldenmatch.core.autoconfig import auto_configure
            cfg = auto_configure(specs)

        sweeps = []
        for spec in args.get("sweep", []):
            parts = str(spec).split(":")
            if len(parts) != 4:
                return {"error": f"Bad sweep spec '{spec}'; expected 'field:start:stop:step'"}
            sweeps.append(SweepParam(
                field=parts[0],
                start=float(parts[1]),
                stop=float(parts[2]),
                step=float(parts[3]),
            ))
        if not sweeps:
            return {"error": "Provide at least one sweep spec, e.g. 'threshold:0.70:0.95:0.05'"}

        results = run_sensitivity(specs, cfg, sweeps, sample_size=args.get("sample_size"))
        return {"results": [r.stability_report() for r in results]}

    if name == "incremental":
        from pathlib import Path as _Path

        from goldenmatch.core.incremental import run_incremental

        base_file = args["base_file"]
        cfg_path = args.get("config")
        if cfg_path:
            from goldenmatch.config.loader import load_config
            cfg = load_config(cfg_path)
        else:
            from goldenmatch.core.autoconfig import auto_configure
            cfg = auto_configure([(base_file, _Path(base_file).stem)])

        return run_incremental(
            base_file,
            args["new_records"],
            cfg,
            threshold=args.get("threshold"),
        )

    return {"error": f"Unknown agent tool: {name}"}
