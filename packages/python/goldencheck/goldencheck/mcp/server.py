"""MCP server exposing GoldenCheck tools for Claude Desktop and other MCP clients."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from goldencheck.engine.scanner import scan_file, scan_file_with_llm
from goldencheck.engine.confidence import apply_confidence_downgrade
from goldencheck.engine.validator import validate_file
from goldencheck.config.loader import load_config
from goldencheck.models.finding import Finding, Severity

logger = logging.getLogger("goldencheck.mcp")

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS = [
    Tool(
        name="scan",
        description=(
            "Scan a data file (CSV, Parquet, Excel) for data quality issues. "
            "Returns findings with severity, confidence, affected rows, and sample values. "
            "No configuration needed — rules are discovered from the data."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the data file (CSV, Parquet, Excel)",
                },
                "llm_boost": {
                    "type": "boolean",
                    "description": "Enable LLM enhancement (requires API key env var)",
                    "default": False,
                },
                "llm_provider": {
                    "type": "string",
                    "description": "LLM provider: 'anthropic' or 'openai'",
                    "default": "anthropic",
                    "enum": ["anthropic", "openai"],
                },
                "sample_size": {
                    "type": "integer",
                    "description": "Max rows to sample (default 100000)",
                    "default": 100000,
                },
            },
            "required": ["file_path"],
        },
    ),
    Tool(
        name="validate",
        description=(
            "Validate a data file against pinned rules in goldencheck.yml. "
            "Returns validation findings (existence, required, unique, enum, range checks)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the data file",
                },
                "config_path": {
                    "type": "string",
                    "description": "Path to goldencheck.yml (default: ./goldencheck.yml)",
                    "default": "goldencheck.yml",
                },
            },
            "required": ["file_path"],
        },
    ),
    Tool(
        name="profile",
        description=(
            "Profile a data file and return column-level statistics: "
            "type, null%, unique%, min/max, top values, detected formats. "
            "Also returns a health score (A-F) based on finding severity."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the data file",
                },
                "sample_size": {
                    "type": "integer",
                    "description": "Max rows to sample (default 100000)",
                    "default": 100000,
                },
            },
            "required": ["file_path"],
        },
    ),
    Tool(
        name="health_score",
        description=(
            "Get the health score (A-F, 0-100) for a data file. "
            "Quick summary of overall data quality."
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
        name="list_checks",
        description=(
            "List all available profiler checks and what they detect. "
            "No arguments needed."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
    Tool(
        name="get_column_detail",
        description=(
            "Get detailed profile and findings for a specific column."
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
                    "description": "Column name to inspect",
                },
            },
            "required": ["file_path", "column"],
        },
    ),
    Tool(
        name="list_domains",
        description=(
            "List all available domain packs (healthcare, finance, ecommerce, etc.). "
            "Domain packs provide specialized semantic type definitions for specific data domains."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
    Tool(
        name="get_domain_info",
        description=(
            "Get detailed info about a specific domain pack — "
            "lists all semantic types, their name hints, and suppression rules."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "description": "Domain pack name (e.g., healthcare, finance, ecommerce)",
                },
            },
            "required": ["domain"],
        },
    ),
    Tool(
        name="install_domain",
        description=(
            "Download a community domain pack from the goldencheck-types repository "
            "and save it for use in future scans."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "description": "Domain pack name to install",
                },
                "output_path": {
                    "type": "string",
                    "description": "Output path (default: goldencheck_domain.yaml)",
                    "default": "goldencheck_domain.yaml",
                },
            },
            "required": ["domain"],
        },
    ),
]


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

def _serialize_findings(findings: list[Finding]) -> list[dict]:
    results = []
    for f in findings:
        d = asdict(f)
        d["severity"] = f.severity.name
        results.append(d)
    return results


def _tool_scan(arguments: dict) -> dict:
    file_path = arguments["file_path"]
    llm_boost = arguments.get("llm_boost", False)
    llm_provider = arguments.get("llm_provider", "anthropic")
    sample_size = arguments.get("sample_size", 100000)

    path = Path(file_path)
    if not path.exists():
        return {"error": f"File not found: {file_path}"}

    if llm_boost:
        findings, profile = scan_file_with_llm(path, provider=llm_provider)
    else:
        findings, profile = scan_file(path, sample_size=sample_size)
        findings = apply_confidence_downgrade(findings, llm_boost=False)

    errors = sum(1 for f in findings if f.severity == Severity.ERROR)
    warnings = sum(1 for f in findings if f.severity == Severity.WARNING)
    grade, score = profile.health_score(
        findings_by_column=_findings_by_column(findings),
    )

    return {
        "file": str(path),
        "rows": profile.row_count,
        "columns": profile.column_count,
        "health_grade": grade,
        "health_score": score,
        "total_findings": len(findings),
        "errors": errors,
        "warnings": warnings,
        "findings": _serialize_findings(findings),
    }


def _tool_validate(arguments: dict) -> dict:
    file_path = arguments["file_path"]
    config_path = arguments.get("config_path", "goldencheck.yml")

    path = Path(file_path)
    if not path.exists():
        return {"error": f"File not found: {file_path}"}

    cfg = load_config(Path(config_path))
    if cfg is None:
        return {"error": f"No config found at {config_path}. Run scan first."}

    findings = validate_file(path, cfg)

    return {
        "file": str(path),
        "config": str(config_path),
        "total_findings": len(findings),
        "errors": sum(1 for f in findings if f.severity == Severity.ERROR),
        "warnings": sum(1 for f in findings if f.severity == Severity.WARNING),
        "pass": all(f.severity < Severity.ERROR for f in findings),
        "findings": _serialize_findings(findings),
    }


def _tool_profile(arguments: dict) -> dict:
    file_path = arguments["file_path"]
    sample_size = arguments.get("sample_size", 100000)

    path = Path(file_path)
    if not path.exists():
        return {"error": f"File not found: {file_path}"}

    findings, profile = scan_file(path, sample_size=sample_size)
    findings = apply_confidence_downgrade(findings, llm_boost=False)

    grade, score = profile.health_score(
        findings_by_column=_findings_by_column(findings),
    )

    columns = []
    for col in profile.columns:
        columns.append({
            "name": col.name,
            "type": col.inferred_type,
            "null_pct": round(col.null_pct, 2),
            "unique_pct": round(col.unique_pct, 2),
            "row_count": col.row_count,
            "min": col.min_value,
            "max": col.max_value,
            "mean": round(col.mean, 4) if col.mean is not None else None,
            "top_values": col.top_values[:5],
            "detected_format": col.detected_format,
        })

    return {
        "file": str(path),
        "rows": profile.row_count,
        "columns_count": profile.column_count,
        "health_grade": grade,
        "health_score": score,
        "columns": columns,
    }


def _tool_health_score(arguments: dict) -> dict:
    file_path = arguments["file_path"]

    path = Path(file_path)
    if not path.exists():
        return {"error": f"File not found: {file_path}"}

    findings, profile = scan_file(path)
    findings = apply_confidence_downgrade(findings, llm_boost=False)

    grade, score = profile.health_score(
        findings_by_column=_findings_by_column(findings),
    )

    return {
        "file": str(path),
        "grade": grade,
        "score": score,
        "errors": sum(1 for f in findings if f.severity == Severity.ERROR),
        "warnings": sum(1 for f in findings if f.severity == Severity.WARNING),
    }


def _tool_list_checks(_arguments: dict) -> dict:
    return {
        "checks": [
            {
                "name": "type_inference",
                "description": "Detects columns stored as wrong types (e.g., numbers as strings)",
            },
            {
                "name": "nullability",
                "description": "Identifies required vs optional columns based on null patterns",
            },
            {
                "name": "uniqueness",
                "description": "Finds primary key candidates and near-duplicate columns",
            },
            {
                "name": "format_detection",
                "description": "Detects emails, phones, URLs, dates and validates format consistency",
            },
            {
                "name": "range_distribution",
                "description": "Finds outliers and suggests min/max bounds for numeric columns",
            },
            {
                "name": "cardinality",
                "description": "Identifies low-cardinality columns that should be enums",
            },
            {
                "name": "pattern_consistency",
                "description": "Detects mixed formats within a column (e.g., phone number formats)",
            },
            {
                "name": "encoding_detection",
                "description": "Detects mojibake, mixed encodings, and control characters",
            },
            {
                "name": "sequence_detection",
                "description": "Identifies broken auto-increment sequences and gaps",
            },
            {
                "name": "drift_detection",
                "description": "Finds temporal distribution shifts within a column",
            },
            {
                "name": "temporal_order",
                "description": "Cross-column: detects start_date > end_date violations",
            },
            {
                "name": "null_correlation",
                "description": "Cross-column: finds columns that are null together",
            },
            {
                "name": "cross_column_validation",
                "description": "Cross-column: detects value > max constraint violations (e.g., claim > policy_max)",
            },
            {
                "name": "cross_column",
                "description": "Cross-column: detects age vs DOB mismatches and other semantic inconsistencies",
            },
        ]
    }


def _tool_get_column_detail(arguments: dict) -> dict:
    file_path = arguments["file_path"]
    column_name = arguments["column"]

    path = Path(file_path)
    if not path.exists():
        return {"error": f"File not found: {file_path}"}

    findings, profile = scan_file(path)
    findings = apply_confidence_downgrade(findings, llm_boost=False)

    col_profile = next((c for c in profile.columns if c.name == column_name), None)
    if col_profile is None:
        available = [c.name for c in profile.columns]
        return {"error": f"Column '{column_name}' not found. Available: {available}"}

    col_findings = [f for f in findings if f.column == column_name]

    return {
        "column": column_name,
        "type": col_profile.inferred_type,
        "null_pct": round(col_profile.null_pct, 2),
        "unique_pct": round(col_profile.unique_pct, 2),
        "row_count": col_profile.row_count,
        "min": col_profile.min_value,
        "max": col_profile.max_value,
        "mean": round(col_profile.mean, 4) if col_profile.mean is not None else None,
        "stddev": round(col_profile.stddev, 4) if col_profile.stddev is not None else None,
        "top_values": col_profile.top_values[:10],
        "detected_format": col_profile.detected_format,
        "detected_patterns": col_profile.detected_patterns,
        "enum_values": col_profile.enum_values,
        "findings": _serialize_findings(col_findings),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _findings_by_column(findings: list[Finding]) -> dict[str, dict[str, int]]:
    by_col: dict[str, dict[str, int]] = {}
    for f in findings:
        if f.severity >= Severity.WARNING:
            by_col.setdefault(f.column, {"errors": 0, "warnings": 0})
            key = "errors" if f.severity == Severity.ERROR else "warnings"
            by_col[f.column][key] = by_col[f.column].get(key, 0) + 1
    return by_col


def _tool_list_domains(_arguments: dict) -> dict:
    from goldencheck.semantic.classifier import list_available_domains
    import yaml

    domains = []
    for name in list_available_domains():
        domain_path = Path(__file__).parent.parent / "semantic" / "domains" / f"{name}.yaml"
        desc = ""
        types_count = 0
        if domain_path.exists():
            with open(domain_path) as f:
                data = yaml.safe_load(f) or {}
            desc = data.get("description", "")
            types_count = len(data.get("types", {}))
        domains.append({
            "name": name,
            "description": desc,
            "types_count": types_count,
            "source": "bundled",
        })
    return {"domains": domains}


def _tool_get_domain_info(arguments: dict) -> dict:
    import yaml

    domain = arguments["domain"]
    domain_path = Path(__file__).parent.parent / "semantic" / "domains" / f"{domain}.yaml"
    if not domain_path.exists():
        from goldencheck.semantic.classifier import list_available_domains
        available = list_available_domains()
        return {"error": f"Unknown domain: '{domain}'. Available: {', '.join(available)}"}

    with open(domain_path) as f:
        data = yaml.safe_load(f) or {}

    types_info = {}
    for name, cfg in data.get("types", {}).items():
        types_info[name] = {
            "name_hints": cfg.get("name_hints", []),
            "suppress": cfg.get("suppress", []),
        }

    return {
        "name": domain,
        "description": data.get("description", ""),
        "types": types_info,
    }


def _tool_install_domain(arguments: dict) -> dict:
    import re
    import urllib.request

    domain = arguments["domain"]
    output_path = arguments.get("output_path", "goldencheck_domain.yaml")

    # Validate domain name (alphanumeric + hyphens/underscores only)
    if not re.match(r'^[a-z0-9_-]+$', domain):
        return {"error": f"Invalid domain name: '{domain}'. Use lowercase letters, numbers, hyphens, underscores."}

    # Prevent path traversal
    resolved = Path(output_path).resolve()
    cwd = Path.cwd().resolve()
    if not str(resolved).startswith(str(cwd)):
        return {"error": "Output path must be within the working directory."}

    url = f"https://raw.githubusercontent.com/benzsevern/goldencheck-types/main/domains/{domain}.yaml"

    try:
        resp = urllib.request.urlopen(url, timeout=10)
        content = resp.read()
        with open(resolved, "wb") as f:
            f.write(content)
    except Exception as e:
        return {"error": f"Failed to download domain '{domain}': {e}"}

    return {
        "installed": domain,
        "path": str(output_path),
    }


_TOOL_HANDLERS = {
    "scan": _tool_scan,
    "validate": _tool_validate,
    "profile": _tool_profile,
    "health_score": _tool_health_score,
    "list_checks": _tool_list_checks,
    "get_column_detail": _tool_get_column_detail,
    "list_domains": _tool_list_domains,
    "get_domain_info": _tool_get_domain_info,
    "install_domain": _tool_install_domain,
}

# ---------------------------------------------------------------------------
# Agent tools (optional — requires goldencheck.agent extras)
# ---------------------------------------------------------------------------

try:
    from goldencheck.mcp.agent_tools import AGENT_TOOLS, _AGENT_TOOL_HANDLERS

    TOOLS.extend(AGENT_TOOLS)
    _TOOL_HANDLERS.update(_AGENT_TOOL_HANDLERS)
except Exception:  # noqa: BLE001
    logger.debug("Agent tools not available (missing agent extras)")



# ---------------------------------------------------------------------------
# Server factory
# ---------------------------------------------------------------------------

def create_server() -> Server:
    server = Server("goldencheck")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return TOOLS

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        handler = _TOOL_HANDLERS.get(name)
        if handler is None:
            result = {"error": f"Unknown tool: {name}"}
        else:
            try:
                result = handler(arguments)
            except Exception as exc:
                logger.exception("Tool %s failed", name)
                result = {"error": str(exc)}

        return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

    return server


async def run_server() -> None:
    server = create_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())
