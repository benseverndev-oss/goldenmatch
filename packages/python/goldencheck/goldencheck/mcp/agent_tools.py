"""Agent-level MCP tools for GoldenCheck — strategy, review, handoff, and explanation."""
from __future__ import annotations

import logging
from dataclasses import asdict
from pathlib import Path

from mcp.types import Tool

from goldencheck.agent.intelligence import (
    build_alternatives,
    compare_domains,
    explain_column,
    explain_finding,
    select_strategy,
)
from goldencheck.agent.handoff import generate_handoff
from goldencheck.agent.review_queue import ReviewQueue
from goldencheck.engine.confidence import apply_confidence_downgrade
from goldencheck.engine.fixer import apply_fixes
from goldencheck.engine.reader import read_file
from goldencheck.engine.scanner import scan_file
from goldencheck.engine.triage import auto_triage
from goldencheck.models.finding import Finding, Severity

logger = logging.getLogger("goldencheck.mcp.agent")

# ---------------------------------------------------------------------------
# Shared review queue instance (created on first use)
# ---------------------------------------------------------------------------

_review_queue: ReviewQueue | None = None


def _get_review_queue() -> ReviewQueue:
    global _review_queue  # noqa: PLW0603
    if _review_queue is None:
        _review_queue = ReviewQueue()
    return _review_queue


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

AGENT_TOOLS = [
    Tool(
        name="analyze_data",
        description=(
            "Analyze a data file to detect its domain, profile columns, and recommend "
            "a scanning strategy. Returns domain detection, column count, row count, "
            "strategy decisions, and alternative approaches."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the data file (CSV, Parquet, Excel)",
                },
            },
            "required": ["file_path"],
        },
    ),
    Tool(
        name="auto_configure",
        description=(
            "Scan a data file, triage findings by confidence, and generate "
            "goldencheck.yml content from the pinned findings. Optionally accepts "
            "constraints to filter or adjust the generated config."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the data file",
                },
                "constraints": {
                    "type": "object",
                    "description": (
                        "Optional constraints: {min_confidence, severity_filter, "
                        "include_columns, exclude_columns}"
                    ),
                },
            },
            "required": ["file_path"],
        },
    ),
    Tool(
        name="explain_finding",
        description=(
            "Explain a single finding in natural language. Requires the finding "
            "as a JSON dict and the file_path to load a profile for context."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the data file (needed for profile context)",
                },
                "finding": {
                    "type": "object",
                    "description": (
                        "Finding dict with keys: severity, column, check, message, "
                        "affected_rows, confidence, sample_values"
                    ),
                },
            },
            "required": ["file_path", "finding"],
        },
    ),
    Tool(
        name="explain_column",
        description=(
            "Get a natural-language health narrative for a specific column. "
            "Scans the file, profiles the column, and explains all findings."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the data file",
                },
                "column": {
                    "type": "string",
                    "description": "Column name to explain",
                },
            },
            "required": ["file_path", "column"],
        },
    ),
    Tool(
        name="review_queue",
        description=(
            "List all pending review items for a given job. Returns items that "
            "need human decision (medium-confidence findings)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "job_name": {
                    "type": "string",
                    "description": "Job name to filter review items",
                },
            },
            "required": ["job_name"],
        },
    ),
    Tool(
        name="approve_reject",
        description=(
            "Approve (pin) or reject (dismiss) a review queue item. "
            "Decision must be 'pin' or 'dismiss'."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "item_id": {
                    "type": "string",
                    "description": "Review item ID to update",
                },
                "decision": {
                    "type": "string",
                    "description": "Decision: 'pin' (approve) or 'dismiss' (reject)",
                    "enum": ["pin", "dismiss"],
                },
                "reason": {
                    "type": "string",
                    "description": "Optional reason for the decision",
                },
            },
            "required": ["item_id", "decision"],
        },
    ),
    Tool(
        name="compare_domains",
        description=(
            "Scan a file with every available domain pack (plus base/no-domain) "
            "and compare health scores. Recommends the best-fitting domain."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the data file",
                },
            },
            "required": ["file_path"],
        },
    ),
    Tool(
        name="suggest_fix",
        description=(
            "Preview fixes for a data file without applying them. Shows what "
            "would change (columns, fix types, rows affected, before/after samples)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the data file",
                },
                "mode": {
                    "type": "string",
                    "description": "Fix mode: 'safe' (default) or 'aggressive'",
                    "default": "safe",
                    "enum": ["safe", "aggressive"],
                },
            },
            "required": ["file_path"],
        },
    ),
    Tool(
        name="pipeline_handoff",
        description=(
            "Generate a structured quality attestation JSON for a data file. "
            "Includes health score, findings summary, pinned rules, and attestation "
            "status (PASS, PASS_WITH_WARNINGS, REVIEW_REQUIRED, FAIL)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the data file",
                },
                "job_name": {
                    "type": "string",
                    "description": "Job name for the handoff record",
                },
            },
            "required": ["file_path", "job_name"],
        },
    ),
    Tool(
        name="review_stats",
        description=(
            "Get review queue statistics for a job — counts of pending, "
            "pinned, and dismissed items."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "job_name": {
                    "type": "string",
                    "description": "Job name to get stats for",
                },
            },
            "required": ["job_name"],
        },
    ),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _serialize_findings(findings: list[Finding]) -> list[dict]:
    results = []
    for f in findings:
        d = asdict(f)
        d["severity"] = f.severity.name
        results.append(d)
    return results


def _finding_from_dict(d: dict) -> Finding:
    """Reconstruct a Finding from a JSON dict."""
    severity_map = {"ERROR": Severity.ERROR, "WARNING": Severity.WARNING, "INFO": Severity.INFO}
    return Finding(
        severity=severity_map.get(d.get("severity", "INFO"), Severity.INFO),
        column=d.get("column", ""),
        check=d.get("check", ""),
        message=d.get("message", ""),
        affected_rows=d.get("affected_rows", 0),
        sample_values=d.get("sample_values", []),
        suggestion=d.get("suggestion"),
        pinned=d.get("pinned", False),
        source=d.get("source"),
        confidence=d.get("confidence", 1.0),
        metadata=d.get("metadata", {}),
    )


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


def _tool_analyze_data(arguments: dict) -> dict:
    file_path = arguments["file_path"]
    path = Path(file_path)
    if not path.exists():
        return {"error": f"File not found: {file_path}"}

    df = read_file(path)
    decision = select_strategy(df)
    alternatives = build_alternatives(decision, decision.why.get("domain_scores", {}))

    return {
        "file": str(path),
        "rows": len(df),
        "columns": len(df.columns),
        "column_names": df.columns,
        "strategy": {
            "domain": decision.domain,
            "domain_confidence": round(decision.domain_confidence, 3),
            "sample_strategy": decision.sample_strategy,
            "profiler_strategy": decision.profiler_strategy,
            "llm_boost": decision.llm_boost,
        },
        "reasoning": decision.why,
        "alternatives": alternatives,
    }


def _tool_auto_configure(arguments: dict) -> dict:
    file_path = arguments["file_path"]
    constraints = arguments.get("constraints", {})
    path = Path(file_path)
    if not path.exists():
        return {"error": f"File not found: {file_path}"}

    findings, profile = scan_file(path)
    findings = apply_confidence_downgrade(findings, llm_boost=False)
    triage = auto_triage(findings)

    pinned = triage.pin

    # Apply optional constraints
    min_conf = constraints.get("min_confidence", 0.0)
    if min_conf > 0:
        pinned = [f for f in pinned if f.confidence >= min_conf]

    sev_filter = constraints.get("severity_filter")
    if sev_filter:
        sev_map = {"ERROR": Severity.ERROR, "WARNING": Severity.WARNING, "INFO": Severity.INFO}
        target = sev_map.get(sev_filter.upper())
        if target is not None:
            pinned = [f for f in pinned if f.severity >= target]

    include_cols = constraints.get("include_columns")
    if include_cols:
        pinned = [f for f in pinned if f.column in include_cols]

    exclude_cols = constraints.get("exclude_columns")
    if exclude_cols:
        pinned = [f for f in pinned if f.column not in exclude_cols]

    # Build YAML-ready rules
    rules: list[dict] = []
    for f in pinned:
        rule: dict = {
            "column": f.column,
            "check": f.check,
            "severity": f.severity.name,
            "message": f.message,
        }
        if f.suggestion:
            rule["suggestion"] = f.suggestion
        if f.metadata:
            rule["metadata"] = f.metadata
        rules.append(rule)

    yaml_content = _rules_to_yaml(rules, profile)

    return {
        "file": str(path),
        "pinned_count": len(pinned),
        "review_count": len(triage.review),
        "dismissed_count": len(triage.dismiss),
        "rules": rules,
        "yaml_content": yaml_content,
    }


def _rules_to_yaml(rules: list[dict], profile) -> str:
    """Build a minimal goldencheck.yml string from pinned rules."""
    import yaml

    config: dict = {
        "version": 1,
        "dataset": {
            "row_count": profile.row_count,
            "column_count": profile.column_count,
        },
        "rules": rules,
    }
    return yaml.dump(config, default_flow_style=False, sort_keys=False)


def _tool_explain_finding(arguments: dict) -> dict:
    file_path = arguments["file_path"]
    finding_dict = arguments["finding"]
    path = Path(file_path)
    if not path.exists():
        return {"error": f"File not found: {file_path}"}

    finding = _finding_from_dict(finding_dict)
    _findings, profile = scan_file(path)

    return explain_finding(finding, profile)


def _tool_explain_column(arguments: dict) -> dict:
    file_path = arguments["file_path"]
    column = arguments["column"]
    path = Path(file_path)
    if not path.exists():
        return {"error": f"File not found: {file_path}"}

    return explain_column(file_path, column)


def _tool_review_queue(arguments: dict) -> dict:
    job_name = arguments["job_name"]
    queue = _get_review_queue()
    pending = queue.pending(job_name)

    items = []
    for item in pending:
        items.append({
            "item_id": item.item_id,
            "column": item.column,
            "check": item.check,
            "severity": item.severity,
            "confidence": item.confidence,
            "message": item.message,
            "explanation": item.explanation,
            "sample_values": item.sample_values,
        })

    return {
        "job_name": job_name,
        "pending_count": len(items),
        "items": items,
    }


def _tool_approve_reject(arguments: dict) -> dict:
    item_id = arguments["item_id"]
    decision = arguments["decision"]
    reason = arguments.get("reason", "")
    queue = _get_review_queue()

    try:
        if decision == "pin":
            queue.approve(item_id, decided_by="mcp_agent", reason=reason)
        else:
            queue.reject(item_id, decided_by="mcp_agent", reason=reason)
    except KeyError:
        return {"error": f"Review item not found: {item_id}"}

    return {
        "item_id": item_id,
        "decision": decision,
        "reason": reason,
        "status": "updated",
    }


def _tool_compare_domains(arguments: dict) -> dict:
    file_path = arguments["file_path"]
    path = Path(file_path)
    if not path.exists():
        return {"error": f"File not found: {file_path}"}

    return compare_domains(file_path)


def _tool_suggest_fix(arguments: dict) -> dict:
    file_path = arguments["file_path"]
    mode = arguments.get("mode", "safe")
    path = Path(file_path)
    if not path.exists():
        return {"error": f"File not found: {file_path}"}

    df = read_file(path)
    findings, _profile = scan_file(path)
    findings = apply_confidence_downgrade(findings, llm_boost=False)

    # Run fixes in the requested mode — aggressive requires force=True
    _fixed_df, report = apply_fixes(df, findings, mode=mode, force=(mode == "aggressive"))

    entries = []
    for entry in report.entries:
        entries.append({
            "column": entry.column,
            "fix_type": entry.fix_type,
            "rows_affected": entry.rows_affected,
            "sample_before": entry.sample_before[:5],
            "sample_after": entry.sample_after[:5],
        })

    return {
        "file": str(path),
        "mode": mode,
        "total_fixes": len(entries),
        "total_rows_fixed": report.total_rows_fixed,
        "fixes": entries,
    }


def _tool_pipeline_handoff(arguments: dict) -> dict:
    file_path = arguments["file_path"]
    job_name = arguments["job_name"]
    path = Path(file_path)
    if not path.exists():
        return {"error": f"File not found: {file_path}"}

    findings, profile = scan_file(path)
    findings = apply_confidence_downgrade(findings, llm_boost=False)
    triage = auto_triage(findings)

    # Build pinned rules list
    pinned_rules = []
    for f in triage.pin:
        pinned_rules.append({
            "column": f.column,
            "check": f.check,
            "severity": f.severity.name,
            "message": f.message,
        })

    handoff = generate_handoff(
        file_path=file_path,
        findings=findings,
        profile=profile,
        pinned_rules=pinned_rules,
        review_pending=len(triage.review),
        dismissed=len(triage.dismiss),
        job_name=job_name,
    )

    return handoff


def _tool_review_stats(arguments: dict) -> dict:
    job_name = arguments["job_name"]
    queue = _get_review_queue()
    stats = queue.stats(job_name)

    return {
        "job_name": job_name,
        **stats,
    }


# ---------------------------------------------------------------------------
# Handler registry
# ---------------------------------------------------------------------------

_AGENT_TOOL_HANDLERS = {
    "analyze_data": _tool_analyze_data,
    "auto_configure": _tool_auto_configure,
    "explain_finding": _tool_explain_finding,
    "explain_column": _tool_explain_column,
    "review_queue": _tool_review_queue,
    "approve_reject": _tool_approve_reject,
    "compare_domains": _tool_compare_domains,
    "suggest_fix": _tool_suggest_fix,
    "pipeline_handoff": _tool_pipeline_handoff,
    "review_stats": _tool_review_stats,
}
