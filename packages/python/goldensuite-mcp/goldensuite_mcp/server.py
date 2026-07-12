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
import os
from collections.abc import Callable
from typing import Any

from mcp.server import Server
from mcp.types import TextContent, Tool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Curated tool listing
# ---------------------------------------------------------------------------
# The aggregator composes ~88 tools across six packages. A flat namespace that
# large swamps LLM tool-selection, so ``list_tools`` is filtered to a curated
# headline set by default. Dispatch is NEVER filtered, so every hidden tool
# stays callable by exact name -- the filter only trims what the client sees
# when it enumerates tools.
#
# The ``GOLDENSUITE_MCP_TOOLS`` env var overrides the listing:
#   unset / "curated"  -> the headline set below (default)
#   "full"             -> every aggregated tool (the pre-curation behavior)
#   "a,b,c"            -> exactly those names (whitespace tolerated)
#
# Names are matched AFTER first-wins collision resolution, so each name here
# resolves to exactly one surviving tool (e.g. ``map`` is goldenflow's,
# ``validate`` is goldencheck's).
CURATED_TOOLS: frozenset[str] = frozenset({
    # goldenmatch -- entity resolution / dedupe (the headline package)
    "upload_dataset", "analyze_data", "auto_configure",
    "agent_deduplicate", "agent_match_sources", "find_duplicates",
    "match_record", "get_golden_record", "list_clusters", "get_cluster",
    "explain_match", "evaluate", "export_results",
    # goldencheck -- data quality
    "scan", "validate", "health_score", "explain_finding",
    # goldenflow -- transforms / mapping
    "transform", "map", "list_transforms",
    # goldenpipe -- pipeline orchestration
    "run_pipeline", "list_stages",
    # infermap -- schema mapping (``map`` is goldenflow's; ``apply`` is infermap's)
    "apply",
    # goldenanalysis -- read-only run analysis
    "analyze_frame", "detect_regressions",
    # discovery meta-tool -- how a client reaches the ~80 non-headline tools
    "suite_find_tools",
    # composite workflow tools -- one-call happy paths
    "dedupe_file", "match_sources", "assess_file", "clean_and_dedupe",
})


# Suite-only disambiguation suffixes appended to a curated tool's own
# description in the ``list_tools`` headline view. These say things only the
# SUITE knows -- that a one-call composite covers the same job, or that a tool
# needs prior session state -- which the underlying package's standalone
# description can't (composites don't exist at the package level). Applied
# non-destructively (model_copy) to the curated list ONLY; dispatch and the
# full catalog / suite_find_tools keep each package's base description.
# Every key MUST be in CURATED_TOOLS (enforced by test).
#
# NOTE: suffixes for the session-stateful goldenmatch tools (list_clusters,
# get_cluster, get_golden_record, explain_match, evaluate, export_results,
# match_record, find_duplicates) were deliberately NOT added. Those tools read
# module-global run state populated only by the standalone goldenmatch server's
# startup (`create_server(file_paths=...)`); the aggregator imports gm.TOOLS /
# gm.dispatch directly and never sets it, so they currently raise AttributeError
# via the suite endpoint regardless of any prior call (agent_deduplicate uses a
# separate stateless AgentSession). Telling an LLM to "run agent_deduplicate
# first" would be a plausible-but-false remediation. Tracked as a separate bug
# (curate-out or wire the state); no misleading suffix here.
_CURATED_DESCRIPTION_SUFFIXES: dict[str, str] = {
    # composite one-call alternatives (the primitive points at the composite)
    "agent_deduplicate": (
        "For a one-call flow that uploads a local file and writes the golden CSV "
        "in a single step, use `dedupe_file`; use this tool when you want the "
        "clusters and reasoning returned inline."
    ),
    "agent_match_sources": (
        "For a one-call upload-both-and-write flow, use `match_sources`; use this "
        "tool for the matched pairs returned inline."
    ),
    "analyze_data": "For profiling PLUS a data-quality scan in one call, use `assess_file`.",
    "transform": "To clean and then deduplicate in one call, use `clean_and_dedupe`.",
    # map (goldenflow) vs apply (infermap) -- easily confused names
    "map": (
        "Distinct from `apply` (infermap): `map` auto-DERIVES a column alignment "
        "between two files, while `apply` applies an already-saved mapping config."
    ),
    "apply": (
        "Distinct from `map` (GoldenFlow): `apply` applies an already-saved "
        "infermap mapping config, while `map` auto-derives a new alignment."
    ),
}


def _with_curated_suffix(tool: Tool) -> Tool:
    """Return *tool* with its suite disambiguation suffix appended, or unchanged.

    Non-destructive: emits a copy so the aggregated Tool (shared with dispatch
    and the full catalog) keeps its base description.
    """
    suffix = _CURATED_DESCRIPTION_SUFFIXES.get(tool.name)
    if not suffix:
        return tool
    base = (tool.description or "").rstrip()
    joined = f"{base} {suffix}" if base else suffix
    return tool.model_copy(update={"description": joined})


def _resolve_tool_allowlist() -> frozenset[str] | None:
    """Parse ``GOLDENSUITE_MCP_TOOLS`` into an allow-set, or None for 'full'."""
    val = os.environ.get("GOLDENSUITE_MCP_TOOLS", "").strip()
    if not val or val.lower() == "curated":
        return CURATED_TOOLS
    if val.lower() == "full":
        return None
    return frozenset(part.strip() for part in val.split(",") if part.strip())


def _apply_tool_filter(tools: list[Tool]) -> list[Tool]:
    """Filter the aggregated tool list for ``list_tools`` per the env profile.

    In the curated / explicit-subset views, curated tools also get their
    suite-only disambiguation suffix (:data:`_CURATED_DESCRIPTION_SUFFIXES`)
    appended. The ``full`` view is returned verbatim (base descriptions).
    """
    allow = _resolve_tool_allowlist()
    if allow is None:
        return tools
    return [_with_curated_suffix(t) for t in tools if t.name in allow]

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

    # Exclude goldenmatch's internal Python<->TS naming aliases from the
    # aggregated surface: the suite has one surface per operation, and the
    # `profile` alias would otherwise shadow goldencheck's `profile` tool.
    # gm.dispatch still resolves aliases (harmless — they're just never listed here).
    aliases = set(gm._MCP_TOOL_ALIASES)
    tools = [t for t in gm.TOOLS if t.name not in aliases]
    return _normalize_tools(tools), gm.dispatch


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


def _adapt_goldenanalysis() -> tuple[list[Tool], Callable[[str, dict], dict]]:
    from goldenanalysis.mcp import server as ga

    handlers = ga.HANDLERS

    def dispatch(name: str, args: dict) -> dict:
        h = handlers.get(name)
        if h is None:
            return {"error": f"goldenanalysis: unknown tool {name!r}"}
        return h(args)

    return _normalize_tools(list(ga.TOOLS)), dispatch


# Order determines first-wins precedence on tool-name collisions.
# Goldenmatch is the headline package, so its tools win; the others register
# only their unshadowed names. Adjust this order to change precedence.
_SUITE_ORDER: list[tuple[str, Callable[[], tuple[list[Tool], Callable[[str, dict], dict]]]]] = [
    ("goldenmatch", _adapt_goldenmatch),
    ("goldencheck", _adapt_goldencheck),
    ("goldenflow", _adapt_goldenflow),
    ("goldenpipe", _adapt_goldenpipe),
    ("infermap", _adapt_infermap),
    ("goldenanalysis", _adapt_goldenanalysis),
]


# ---------------------------------------------------------------------------
# Discovery meta-tool
# ---------------------------------------------------------------------------
# `list_tools` shows only the curated headline set by default (see above), which
# leaves the long tail undiscoverable even though every tool stays callable by
# exact name. `suite_find_tools` closes that gap: it returns the FULL catalog
# (name + package + description + inputSchema), optionally filtered by keyword or
# package, so a client can find a hidden tool and then call it directly. This is
# the progressive-disclosure pattern -- a small default surface plus one search
# tool -- rather than collapsing everything into overloaded god-tools.

_FIND_TOOLS_NAME = "suite_find_tools"

_FIND_TOOLS_TOOL = Tool(
    name=_FIND_TOOLS_NAME,
    description=(
        "Search the full Golden Suite tool catalog. list_tools shows only a curated "
        "headline subset by default; use this to discover the complete set (~105 tools "
        "across goldenmatch, goldencheck, goldenflow, goldenpipe, infermap, "
        "goldenanalysis), then call any returned tool by its exact `name` (hidden tools "
        "are fully callable, just not listed). Returns each tool's name, package, "
        "description, and inputSchema."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Case-insensitive substring matched against tool name and description. Omit to list everything.",
            },
            "package": {
                "type": "string",
                "enum": [name for name, _ in _SUITE_ORDER],
                "description": "Restrict results to one sub-package.",
            },
        },
        "required": [],
    },
)


def _make_find_tools_dispatch(
    catalog: list[tuple[Tool, str]],
) -> Callable[[str, dict], dict]:
    """Build the dispatcher for `suite_find_tools` over a snapshot of the catalog.

    `catalog` is a list of (tool, source_package) for every real (non-meta) tool
    in the aggregated surface.
    """

    def dispatch(name: str, args: dict) -> dict:
        query = str(args.get("query") or "").strip().lower()
        package = str(args.get("package") or "").strip().lower()
        results: list[dict[str, Any]] = []
        for tool, source in catalog:
            if package and source != package:
                continue
            if query and query not in tool.name.lower() and query not in (
                (tool.description or "").lower()
            ):
                continue
            results.append(
                {
                    "name": tool.name,
                    "package": source,
                    "description": tool.description,
                    "inputSchema": tool.inputSchema,
                }
            )
        return {"count": len(results), "tools": results}

    return dispatch


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

    # Register composite workflow tools BEFORE the discovery snapshot so they
    # appear both in the catalog and in suite_find_tools. They dispatch against
    # the aggregated real-tool table (name_to_dispatch) built above.
    from goldensuite_mcp.composites import build_composites
    composite_tools, composite_dispatch = build_composites(name_to_dispatch)
    for tool in composite_tools:
        if tool.name in seen:
            logger.warning("composite %r shadowed by earlier %s", tool.name, seen[tool.name])
            continue
        seen[tool.name] = "goldensuite"
        all_tools.append(tool)
        name_to_dispatch[tool.name] = composite_dispatch[tool.name]

    # Register the discovery meta-tool over a snapshot of the real tools (it does
    # not list itself). Its name can't collide -- no sub-package ships it -- but
    # guard anyway so a future clash is visible rather than silently shadowing.
    if _FIND_TOOLS_NAME not in seen:
        catalog = [(t, seen[t.name]) for t in all_tools]
        all_tools.append(_FIND_TOOLS_TOOL)
        name_to_dispatch[_FIND_TOOLS_NAME] = _make_find_tools_dispatch(catalog)
        seen[_FIND_TOOLS_NAME] = "goldensuite"
    else:
        logger.warning(
            "goldensuite-mcp: %r already registered by %s; discovery meta-tool not added",
            _FIND_TOOLS_NAME, seen[_FIND_TOOLS_NAME],
        )

    return all_tools, name_to_dispatch


def create_server() -> Server:
    """Build the aggregated Server."""
    tools, dispatch_by_name = _aggregate()
    listed = _apply_tool_filter(tools)
    if len(listed) != len(tools):
        logger.info(
            "goldensuite-mcp: listing %d/%d tools (GOLDENSUITE_MCP_TOOLS profile); "
            "hidden tools remain callable by exact name",
            len(listed), len(tools),
        )

    server = Server("goldensuite-mcp")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return listed

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
