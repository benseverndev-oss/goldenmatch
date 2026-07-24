"""GoldenMatch MCP Server — tools for entity resolution via Claude Desktop.

Usage:
    goldenmatch mcp-serve --file customers.csv --config config.yaml

Or add to Claude Desktop config (claude_desktop_config.json):
    {
        "mcpServers": {
            "goldenmatch": {
                "command": "goldenmatch",
                "args": ["mcp-serve", "--file", "customers.csv"]
            }
        }
    }
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    Prompt,
    PromptArgument,
    PromptMessage,
    Resource,
    TextContent,
    Tool,
)

from goldenmatch.core._paths import PathOutsideAllowedRootError, safe_path
from goldenmatch.mcp.agent_tools import AGENT_TOOLS, handle_agent_tool
from goldenmatch.mcp.document_tools import (
    DOCUMENT_TOOL_NAMES,
    DOCUMENT_TOOLS,
    handle_document_tool,
)
from goldenmatch.mcp.identity_tools import (
    IDENTITY_TOOL_NAMES,
    IDENTITY_TOOLS,
    handle_identity_tool,
)
from goldenmatch.mcp.memory_tools import (
    _MEMORY_TOOL_NAMES,
    MEMORY_TOOLS,
    handle_memory_tool,
)
from goldenmatch.mcp.routing_tools import (
    ROUTING_TOOL_NAMES,
    ROUTING_TOOLS,
    handle_routing_tool,
)

logger = logging.getLogger(__name__)

_AGENT_TOOL_NAMES = frozenset(t.name for t in AGENT_TOOLS)

# Global state
_engine = None
_config = None
_result = None
_rows: list[dict] = []
_id_to_idx: dict[int, int] = {}


def _initialize(file_paths: list[str], config_path: str | None = None) -> None:
    """Load data and run initial matching."""
    global _engine, _config, _result, _rows, _id_to_idx

    from goldenmatch.tui.engine import MatchEngine

    _engine = MatchEngine(file_paths)
    logger.info("Loaded %d records from %d files", _engine.row_count, len(file_paths))

    if config_path:
        from goldenmatch.config.loader import load_config
        _config = load_config(config_path)
    else:
        from goldenmatch.core.autoconfig import auto_configure
        parsed = [(f, Path(f).stem) for f in file_paths]
        _config = auto_configure(parsed)
        logger.info("Auto-configured matching rules")

    _result = _engine.run_full(_config)
    _rows = _engine.data.to_dicts()
    _id_to_idx = {row["__row_id__"]: i for i, row in enumerate(_rows)}
    logger.info(
        "Matching complete: %d clusters, %.1f%% match rate",
        _result.stats.total_clusters, _result.stats.match_rate,
    )



# Base (non-agent) tools — module-level so goldensuite-mcp aggregator can
# import the full goldenmatch tool surface without instantiating a Server.
_BASE_TOOLS = [
    Tool(
        name="get_stats",
        description="Get dataset statistics: record count, cluster count, match rate, cluster sizes.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="find_duplicates",
        description="Find duplicate matches for a record. Provide field values to search against the loaded dataset.",
        inputSchema={
            "type": "object",
            "properties": {
                "record": {
                    "type": "object",
                    "description": "Record fields to match (e.g. {\"name\": \"John Smith\", \"zip\": \"10001\"})",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Max results to return (default 5)",
                    "default": 5,
                },
            },
            "required": ["record"],
        },
    ),
    Tool(
        name="explain_match",
        description="Explain why two records match or don't match. Shows per-field score breakdown.",
        inputSchema={
            "type": "object",
            "properties": {
                "record_a": {
                    "type": "object",
                    "description": "First record fields",
                },
                "record_b": {
                    "type": "object",
                    "description": "Second record fields",
                },
            },
            "required": ["record_a", "record_b"],
        },
    ),
    Tool(
        name="list_clusters",
        description="List duplicate clusters found in the dataset. Returns cluster IDs, sizes, and member counts.",
        inputSchema={
            "type": "object",
            "properties": {
                "min_size": {
                    "type": "integer",
                    "description": "Minimum cluster size to include (default 2)",
                    "default": 2,
                },
                "limit": {
                    "type": "integer",
                    "description": "Max clusters to return (default 20)",
                    "default": 20,
                },
            },
        },
    ),
    Tool(
        name="get_cluster",
        description="Get details of a specific cluster: all member records and their field values.",
        inputSchema={
            "type": "object",
            "properties": {
                "cluster_id": {
                    "type": "integer",
                    "description": "Cluster ID to look up",
                },
            },
            "required": ["cluster_id"],
        },
    ),
    Tool(
        name="get_golden_record",
        description="Get the merged golden (canonical) record for a cluster.",
        inputSchema={
            "type": "object",
            "properties": {
                "cluster_id": {
                    "type": "integer",
                    "description": "Cluster ID",
                },
            },
            "required": ["cluster_id"],
        },
    ),
    Tool(
        name="match_record",
        description=(
            "Match a single record against the loaded dataset in real-time. "
            "Paste a record's fields and instantly see if it matches any existing record. "
            "Uses the configured matchkeys, scorers, and thresholds. "
            "Example: {\"name\": \"John Smith\", \"email\": \"john@test.com\", \"zip\": \"10001\"}"
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "record": {
                    "type": "object",
                    "description": "Record fields to match against the dataset",
                },
                "threshold": {
                    "type": "number",
                    "description": "Minimum score to consider a match (default: use config threshold)",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Max matches to return (default 5)",
                    "default": 5,
                },
            },
            "required": ["record"],
        },
    ),
    Tool(
        name="unmerge_record",
        description=(
            "Remove a record from its cluster. The record becomes a singleton. "
            "Remaining cluster members are re-clustered using stored pair scores. "
            "Use this to fix bad merges."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "record_id": {
                    "type": "integer",
                    "description": "Row ID of the record to unmerge",
                },
            },
            "required": ["record_id"],
        },
    ),
    Tool(
        name="shatter_cluster",
        description=(
            "Break an entire cluster into individual records. "
            "All members become singletons. Use when a cluster is completely wrong."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "cluster_id": {
                    "type": "integer",
                    "description": "Cluster ID to shatter",
                },
            },
            "required": ["cluster_id"],
        },
    ),
    Tool(
        name="suggest_config",
        description=(
            "Analyze bad merges and suggest config changes. "
            "Provide examples of incorrect merges (pairs that should NOT have matched) "
            "and GoldenMatch will identify which fields/thresholds to tighten. "
            "Example: [{\"record_a\": {...}, \"record_b\": {...}, \"reason\": \"different people\"}]"
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "bad_merges": {
                    "type": "array",
                    "description": "List of bad merge examples with record_a, record_b, and optional reason",
                    "items": {
                        "type": "object",
                        "properties": {
                            "record_a": {"type": "object"},
                            "record_b": {"type": "object"},
                            "reason": {"type": "string"},
                        },
                        "required": ["record_a", "record_b"],
                    },
                },
            },
            "required": ["bad_merges"],
        },
    ),
    Tool(
        name="review_config",
        description=(
            "Run the config healer over the loaded dataset: analyze the dedupe "
            "run and return ranked, self-verified suggestions for improving the "
            "matching config (thresholds, scorers, negative evidence, blocking). "
            "Each suggestion carries an id, kind, target, rationale, and a "
            "machine-applicable patch. Requires the native kernel "
            "(pip install goldenmatch[native]); returns an empty list otherwise."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="convert_splink_config",
        description=(
            "Convert a Splink settings JSON (bare or trained) into a GoldenMatch "
            "config. Pass the settings as an inline JSON string -- no filesystem "
            "access needed. Returns the config as YAML, a findings report "
            "(severity/splink_path/message/mapped_to per finding), a summary, "
            "and -- when the Splink input carried trained m/u probabilities -- "
            "the imported EM model as a dict you can persist yourself. "
            "strict=True fails on ANY lossy mapping (not just hard errors)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "settings_json": {
                    "type": "string",
                    "description": "Splink settings (bare or trained model) as a JSON string",
                },
                "strict": {
                    "type": "boolean",
                    "description": "Fail on any lossy mapping (warnings), not just errors (default false)",
                    "default": False,
                },
            },
            "required": ["settings_json"],
        },
    ),
    Tool(
        name="profile_data",
        description="Get data quality profile: column types, null rates, unique counts, sample values.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="export_results",
        description="Export matching results to a file (CSV or JSON).",
        inputSchema={
            "type": "object",
            "properties": {
                "output_path": {
                    "type": "string",
                    "description": "File path to save results",
                },
                "format": {
                    "type": "string",
                    "enum": ["csv", "json"],
                    "description": "Output format (default csv)",
                    "default": "csv",
                },
            },
            "required": ["output_path"],
        },
    ),
    Tool(
        name="list_domains",
        description="List available domain extraction rulebooks (built-in + user-defined).",
        inputSchema={"type": "object", "properties": {}},
    ),
    # Registry-introspection tools (parity with the TS MCP surface): stateless
    # serializers over the config allow-lists, so an agent can discover the valid
    # scorer / transform / survivorship-strategy names before building a config.
    # Core scoring/blocking/clustering PRIMITIVES (reverse-parity with the TS MCP
    # surface, which exposed these while Python did not). Each is a thin, stateless
    # wrapper over a function goldenmatch already had -- an agent can score two
    # strings, score two records, enumerate a file's exact/fuzzy pairs, or cluster
    # a file without first loading a run into session state.
    Tool(
        name="score_strings",
        description="Score similarity between two strings using the requested scorer.",
        inputSchema={
            "type": "object",
            "properties": {
                "a": {"type": "string", "description": "First string"},
                "b": {"type": "string", "description": "Second string"},
                "scorer": {
                    "type": "string",
                    "description": (
                        "Scorer name (exact, jaro_winkler, levenshtein, token_sort, "
                        "soundex_match, dice, jaccard, ensemble)"
                    ),
                },
            },
            "required": ["a", "b"],
        },
    ),
    Tool(
        name="score_pair",
        description="Score two record objects across weighted fields. Returns a combined score.",
        inputSchema={
            "type": "object",
            "properties": {
                "row_a": {"type": "object", "description": "First record"},
                "row_b": {"type": "object", "description": "Second record"},
                "fields": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "field": {"type": "string"},
                            "scorer": {"type": "string"},
                            "weight": {"type": "number"},
                            "transforms": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["field"],
                    },
                },
            },
            "required": ["row_a", "row_b"],
        },
    ),
    Tool(
        name="find_exact_matches",
        description="Find exact matches on a field in a file. Returns pairs.",
        inputSchema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Input file path"},
                "field": {"type": "string", "description": "Field to match on"},
                "transforms": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["path", "field"],
        },
    ),
    Tool(
        name="find_fuzzy_matches",
        description="Find fuzzy matches in a block of rows. Returns scored pairs.",
        inputSchema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Input file path"},
                "field": {"type": "string", "description": "Field to match on"},
                "scorer": {"type": "string"},
                "threshold": {"type": "number"},
                "transforms": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["path", "field"],
        },
    ),
    Tool(
        name="build_clusters",
        description="Group records into clusters given a file and matchkey definition.",
        inputSchema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Input file path"},
                "exact": {"type": "array", "items": {"type": "string"}},
                "fuzzy": {"type": "object", "additionalProperties": {"type": "number"}},
                "blocking": {"type": "array", "items": {"type": "string"}},
                "threshold": {"type": "number"},
            },
            "required": ["path"],
        },
    ),
    # Host helpers (reverse-parity with the TS MCP surface). NOT ER capabilities --
    # they exist so an agent driving this server can stage an input and collect an
    # output without a second toolchain. Both filesystem tools route through
    # `safe_path`, the SAME guard every other file-taking tool here uses
    # (upload_dataset / export_results / the find_* primitives), so they are no more
    # permissive than the existing surface -- see the containment note on the
    # handlers below for what that guard does and does not promise.
    Tool(
        name="server_info",
        description="Return metadata about this GoldenMatch MCP server.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="read_file",
        description="Read a CSV/JSON file and return the first N records.",
        inputSchema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Input file path"},
                "limit": {"type": "number", "description": "Max rows to return (default 100)"},
            },
            "required": ["path"],
        },
    ),
    Tool(
        name="write_csv",
        description="Write a list of record objects to a CSV file.",
        inputSchema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Output file path"},
                "rows": {
                    "type": "array",
                    "items": {"type": "object", "additionalProperties": True},
                },
            },
            "required": ["path", "rows"],
        },
    ),
    Tool(
        name="list_scorers",
        description="List all available similarity scorers.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="list_transforms",
        description="List all available field transforms.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="list_strategies",
        description="List all golden-record survivorship strategies.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="list_blocking_strategies",
        description="List all blocking strategy names.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="create_domain",
        description="Create a custom domain extraction rulebook. Define patterns for a specific data domain (medical devices, automotive parts, real estate, etc.).",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Domain name (e.g. 'medical_devices', 'automotive_parts')",
                },
                "signals": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Column name keywords that trigger this domain (e.g. ['ndc', 'fda', 'implant'])",
                },
                "identifier_patterns": {
                    "type": "object",
                    "description": "Named regex patterns for domain identifiers (e.g. {'ndc': '\\\\b(\\\\d{5}-\\\\d{4}-\\\\d{2})\\\\b'})",
                },
                "brand_patterns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Brand/manufacturer names to extract (e.g. ['Medtronic', 'Abbott'])",
                },
                "attribute_patterns": {
                    "type": "object",
                    "description": "Named regex patterns for domain attributes (e.g. {'size': '\\\\b(\\\\d+mm)\\\\b'})",
                },
                "stop_words": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Words to strip during name normalization",
                },
                "scope": {
                    "type": "string",
                    "enum": ["local", "global"],
                    "description": "Save locally (.goldenmatch/domains/) or globally (~/.goldenmatch/domains/). Default: local.",
                    "default": "local",
                },
            },
            "required": ["name", "signals"],
        },
    ),
    Tool(
        name="test_domain",
        description="Test a domain extraction rulebook against sample records. Shows what features would be extracted from the loaded data.",
        inputSchema={
            "type": "object",
            "properties": {
                "domain_name": {
                    "type": "string",
                    "description": "Name of the domain rulebook to test",
                },
                "sample_size": {
                    "type": "integer",
                    "description": "Number of records to test (default 10)",
                    "default": 10,
                },
            },
            "required": ["domain_name"],
        },
    ),
    Tool(
        name="pprl_auto_config",
        description=(
            "Analyze the loaded dataset and recommend optimal PPRL (privacy-preserving record linkage) configuration. "
            "Returns recommended fields, bloom filter parameters, threshold, and explanation."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "security_level": {
                    "type": "string",
                    "enum": ["standard", "high", "paranoid"],
                    "description": "Security level (default: high)",
                    "default": "high",
                },
                "use_llm": {
                    "type": "boolean",
                    "description": "Use LLM for enhanced recommendations (requires API key)",
                    "default": False,
                },
            },
        },
    ),
    Tool(
        name="pprl_link",
        description=(
            "Run privacy-preserving record linkage between two parties' data. "
            "Computes bloom filters, matches records without sharing raw data. "
            "Specify fields, threshold, and security level."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "file_a": {
                    "type": "string",
                    "description": "Path to party A's CSV file",
                },
                "file_b": {
                    "type": "string",
                    "description": "Path to party B's CSV file",
                },
                "file_a_content": {"type": "string", "description": "Alternative to file_a: base64/text bytes"},
                "file_a_name": {"type": "string"},
                "file_b_content": {"type": "string", "description": "Alternative to file_b: base64/text bytes"},
                "file_b_name": {"type": "string"},
                "encoding": {"type": "string", "enum": ["base64", "text"], "description": "Encoding of *_content (default base64)"},
                "fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Field names to match on (e.g. ['first_name', 'last_name', 'zip_code'])",
                },
                "threshold": {
                    "type": "number",
                    "description": "Match threshold (default: auto-detected)",
                },
                "security_level": {
                    "type": "string",
                    "enum": ["standard", "high", "paranoid"],
                    "default": "high",
                },
            },
            "required": ["fields"],
        },
    ),
    Tool(
        name="evaluate",
        description=(
            "Score the loaded run against ground-truth pairs. Loads a "
            "ground-truth CSV (id_a,id_b columns) and returns precision, "
            "recall, and F1 for the current clustering."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "ground_truth_path": {
                    "type": "string",
                    "description": "CSV of true match pairs (columns id_a,id_b or idA,idB).",
                },
                "col_a": {"type": "string", "default": "id_a"},
                "col_b": {"type": "string", "default": "id_b"},
            },
            "required": ["ground_truth_path"],
        },
    ),
    Tool(
        name="analyze_blocking",
        description=(
            "Diagnose blocking on the loaded dataset: returns ranked blocking "
            "key candidates with block counts, max block size, total candidate "
            "comparisons, and estimated recall. Use it to explain why matching "
            "is slow or produces too many candidate pairs."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "sample_size": {"type": "integer", "default": 1000},
                "target_block_size": {"type": "integer", "default": 5000},
                "limit": {"type": "integer", "default": 10, "description": "Top N suggestions"},
            },
        },
    ),
    Tool(
        name="compare_clusters",
        description=(
            "Compare two ER clustering outcomes on the same dataset without "
            "ground truth (CCMS): classifies each cluster as unchanged / "
            "merged / partitioned / overlapping and returns the Talburt-Wang "
            "Index. Both inputs are JSON cluster files (as written by "
            "export-style output)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "clusters_a_path": {"type": "string", "description": "Baseline clusters JSON"},
                "clusters_b_path": {"type": "string", "description": "Comparison clusters JSON"},
                "clusters_a_content": {"type": "string", "description": "Alternative to clusters_a_path: base64/text bytes (JSON, use encoding='text')"},
                "clusters_a_name": {"type": "string"},
                "clusters_b_content": {"type": "string", "description": "Alternative to clusters_b_path: base64/text bytes (JSON, use encoding='text')"},
                "clusters_b_name": {"type": "string"},
                "encoding": {"type": "string", "enum": ["base64", "text"], "description": "Encoding of *_content (default base64)"},
            },
            "required": [],
        },
    ),
    Tool(
        name="schema_match",
        description=(
            "Auto-map columns between two files with different schemas. "
            "Returns proposed (col_a, col_b) mappings with a confidence score "
            "and method (synonym / name_sim / composite). Useful before "
            "matching two sources."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "file_a": {"type": "string"},
                "file_b": {"type": "string"},
                "file_a_content": {"type": "string", "description": "Alternative to file_a: base64/text bytes"},
                "file_a_name": {"type": "string"},
                "file_b_content": {"type": "string", "description": "Alternative to file_b: base64/text bytes"},
                "file_b_name": {"type": "string"},
                "encoding": {"type": "string", "enum": ["base64", "text"], "description": "Encoding of *_content (default base64)"},
                "min_score": {"type": "number", "default": 0.5},
            },
            "required": [],
        },
    ),
    Tool(
        name="lineage",
        description=(
            "Field-level provenance for the loaded run: for each scored pair, "
            "the per-field scores that produced the match, plus cluster id. "
            "Optionally write a lineage JSON to a directory."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "max_pairs": {"type": "integer", "default": 100},
                "natural_language": {"type": "boolean", "default": False},
                "output_dir": {
                    "type": "string",
                    "description": "If set, write lineage JSON here and return the path instead of inline records.",
                },
            },
        },
    ),
    Tool(
        name="list_runs",
        description="List previous dedupe/match runs (for rollback) from the run log.",
        inputSchema={
            "type": "object",
            "properties": {
                "output_dir": {"type": "string", "default": "."},
            },
        },
    ),
    Tool(
        name="rollback",
        description=(
            "Undo a previous run by DELETING its output files (looked up by "
            "run_id in the run log). Destructive: removes the files that run "
            "wrote. Use list_runs first to find the run_id."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "output_dir": {"type": "string", "default": "."},
            },
            "required": ["run_id"],
        },
    ),
    Tool(
        name="config_weaknesses",
        description=(
            "Diagnose weaknesses in the loaded run's auto-config: columns admitted "
            "that shouldn't be (source/provenance labels, per-row IDs), oversized or "
            "shared-value blocks, null sinks, low-signal matchkeys, and over-merging. "
            "Returns ranked findings, each with a plain-English explanation + a concrete "
            "fix, plus a one-paragraph summary."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "max_findings": {
                    "type": "integer",
                    "description": "Max findings to return, ranked by severity (default 6).",
                    "default": 6,
                },
                "phrasing": {
                    "type": "string",
                    "enum": ["plain", "technical"],
                    "description": "Wording style for the findings (default plain).",
                    "default": "plain",
                },
            },
        },
    ),
]

# --- Cross-language naming aliases (Python<->TS MCP parity) -----------------
# Each alias forwards to an EXISTING handler; no operation logic is duplicated.
# See docs/superpowers/specs/2026-07-05-mcp-naming-aliases-parity-design.md.
_MCP_TOOL_ALIASES = {
    "dedupe": "find_duplicates",
    "match": "match_record",
    "explain_pair": "explain_match",
    "profile": "profile_data",
    "explain_cluster": "agent_explain_cluster",
}


def _resolve_alias(name: str) -> str:
    """Map an alias tool name to its canonical name (identity for non-aliases)."""
    return _MCP_TOOL_ALIASES.get(name, name)


def _build_alias_tools() -> list[Tool]:
    """Derive alias Tool objects from their canonical tools so schemas can't drift.
    Canonicals live in AGENT_TOOLS (agent_explain_cluster) + _BASE_TOOLS (the rest)."""
    canon = {t.name: t for t in AGENT_TOOLS + _BASE_TOOLS}
    tools = []
    for alias, target in _MCP_TOOL_ALIASES.items():
        c = canon[target]
        tools.append(Tool(
            name=alias,
            description=f"Alias for `{target}`. {c.description}",
            inputSchema=c.inputSchema,
        ))
    return tools


# Append aliases to the shared _BASE_TOOLS component so BOTH advertise paths
# (the TOOLS var below AND the inline list_tools rebuild) pick them up.
_BASE_TOOLS += _build_alias_tools()

# TOOLS is the union of agent tools + memory tools + base tools, in the same order list_tools returns.
TOOLS = AGENT_TOOLS + MEMORY_TOOLS + IDENTITY_TOOLS + ROUTING_TOOLS + _BASE_TOOLS + DOCUMENT_TOOLS


def dispatch(name: str, args: dict) -> dict:
    """Unified dispatcher used by goldensuite-mcp aggregator.

    Routes agent-level tool calls to AgentSession via agent_tools._dispatch,
    memory tools via memory_tools._dispatch, and base tool calls to
    _handle_tool. Returns a JSON-serializable dict for all.
    """
    name = _resolve_alias(name)
    if name in _AGENT_TOOL_NAMES:
        from goldenmatch.core.agent import AgentSession
        from goldenmatch.mcp.agent_tools import _dispatch as _agent_dispatch
        return _agent_dispatch(name, args, AgentSession)
    if name in _MEMORY_TOOL_NAMES:
        from goldenmatch.mcp.memory_tools import _dispatch as _memory_dispatch
        return _memory_dispatch(name, args)
    if name in IDENTITY_TOOL_NAMES:
        from goldenmatch.mcp.identity_tools import _dispatch as _identity_dispatch
        return _identity_dispatch(name, args)
    if name in ROUTING_TOOL_NAMES:
        return handle_routing_tool(name, args)
    if name in DOCUMENT_TOOL_NAMES:
        return handle_document_tool(name, args)
    return _handle_tool(name, args)


def create_server(file_paths: list[str] | None = None, config_path: str | None = None) -> Server:
    """Create and configure the MCP server."""

    if file_paths:
        _initialize(file_paths, config_path)

    server = Server("GoldenMatch")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return AGENT_TOOLS + MEMORY_TOOLS + IDENTITY_TOOLS + ROUTING_TOOLS + _BASE_TOOLS + DOCUMENT_TOOLS

    @server.list_resources()
    async def list_resources() -> list[Resource]:
        resources = []
        if _result is not None:
            s = _result.stats
            resources.append(Resource(
                uri="goldenmatch://dataset/stats",
                name="Dataset Statistics",
                description=f"{s.total_records} records, {s.total_clusters} clusters, {round(s.match_rate, 1)}% match rate",
                mimeType="application/json",
            ))
            resources.append(Resource(
                uri="goldenmatch://dataset/clusters",
                name="Cluster Summary",
                description=f"{s.total_clusters} clusters (avg size {round(s.avg_cluster_size, 1)}, max {s.max_cluster_size})",
                mimeType="application/json",
            ))
        if _config is not None:
            resources.append(Resource(
                uri="goldenmatch://config/current",
                name="Current Configuration",
                description="Active matchkeys, thresholds, scorers, and blocking rules",
                mimeType="application/json",
            ))
        if _rows:
            resources.append(Resource(
                uri="goldenmatch://dataset/schema",
                name="Dataset Schema",
                description="Column names, types, and sample values from the loaded data",
                mimeType="application/json",
            ))
        return resources

    @server.read_resource()
    async def read_resource(uri: str) -> str:
        if uri == "goldenmatch://dataset/stats":
            if _result is None:
                return json.dumps({"error": "No dataset loaded"})
            s = _result.stats
            return json.dumps({
                "total_records": s.total_records,
                "total_clusters": s.total_clusters,
                "singleton_count": s.singleton_count,
                "match_rate": round(s.match_rate, 2),
                "avg_cluster_size": round(s.avg_cluster_size, 2),
                "max_cluster_size": s.max_cluster_size,
                "total_pairs": len(_result.scored_pairs),
            }, indent=2)
        elif uri == "goldenmatch://dataset/clusters":
            if _result is None:
                return json.dumps({"error": "No dataset loaded"})
            clusters = {}
            for row in _rows:
                cid = row.get("__cluster_id__")
                if cid is not None:
                    clusters.setdefault(cid, []).append(row.get("__row_id__"))
            summary = [
                {"cluster_id": cid, "size": len(members)}
                for cid, members in sorted(clusters.items(), key=lambda x: -len(x[1]))
                if len(members) > 1
            ][:50]
            return json.dumps({"clusters": summary, "total": len(summary)}, indent=2)
        elif uri == "goldenmatch://config/current":
            if _config is None:
                return json.dumps({"error": "No config loaded"})
            return json.dumps(_config.to_dict(), default=str, indent=2)
        elif uri == "goldenmatch://dataset/schema":
            if not _rows:
                return json.dumps({"error": "No dataset loaded"})
            sample = _rows[0] if _rows else {}
            cols = [
                {"name": k, "type": type(v).__name__, "sample": str(v)[:100]}
                for k, v in sample.items()
                if not k.startswith("__")
            ]
            return json.dumps({"columns": cols, "record_count": len(_rows)}, indent=2)
        else:
            return json.dumps({"error": f"Unknown resource: {uri}"})

    @server.list_prompts()
    async def list_prompts() -> list[Prompt]:
        return [
            Prompt(
                name="deduplicate-walkthrough",
                description="Step-by-step guided deduplication workflow: profile data, configure matching, run, review results, fix bad merges.",
                arguments=[
                    PromptArgument(
                        name="focus",
                        description="What to focus on: 'accuracy' (minimize false positives), 'recall' (minimize missed duplicates), or 'balanced' (default)",
                        required=False,
                    ),
                ],
            ),
            Prompt(
                name="investigate-cluster",
                description="Deep-dive into a specific cluster: explain why records matched, identify potential bad merges, suggest fixes.",
                arguments=[
                    PromptArgument(
                        name="cluster_id",
                        description="The cluster ID to investigate",
                        required=True,
                    ),
                ],
            ),
            Prompt(
                name="compare-records",
                description="Detailed comparison of two records: field-by-field scoring, match/no-match verdict, explanation.",
                arguments=[
                    PromptArgument(
                        name="record_a",
                        description="First record as JSON (e.g. {\"name\": \"John Smith\", \"email\": \"john@test.com\"})",
                        required=True,
                    ),
                    PromptArgument(
                        name="record_b",
                        description="Second record as JSON",
                        required=True,
                    ),
                ],
            ),
            Prompt(
                name="data-quality-audit",
                description="Full data quality audit: profile columns, identify issues, recommend cleaning steps before matching.",
                arguments=[],
            ),
            Prompt(
                name="pprl-setup",
                description="Guide through privacy-preserving record linkage setup: assess data sensitivity, recommend PPRL config, run linkage.",
                arguments=[
                    PromptArgument(
                        name="security_level",
                        description="Security level: 'standard', 'high', or 'maximum'",
                        required=False,
                    ),
                ],
            ),
        ]

    @server.get_prompt()
    async def get_prompt(name: str, arguments: dict | None = None) -> list[PromptMessage]:
        args = arguments or {}

        if name == "deduplicate-walkthrough":
            focus = args.get("focus", "balanced")
            return [PromptMessage(
                role="user",
                content=TextContent(
                    type="text",
                    text=(
                        f"I want to deduplicate my dataset with a '{focus}' focus. Walk me through it step by step:\n\n"
                        "1. First, call `profile_data` to understand the data shape and quality.\n"
                        "2. Review the profile and call `analyze_data` to detect the domain and recommend a strategy.\n"
                        "3. Call `auto_configure` to generate matching config, or help me build one manually.\n"
                        "4. Run `agent_deduplicate` to execute the matching pipeline.\n"
                        "5. Call `get_stats` to show me the results summary.\n"
                        "6. Call `list_clusters` to show the largest clusters.\n"
                        "7. For any suspicious clusters, call `agent_explain_cluster` to check if the merges are correct.\n"
                        "8. If there are bad merges, call `suggest_config` with examples and help me tune.\n"
                        "9. Finally, call `export_results` to save the output.\n\n"
                        "Start with step 1 now."
                    ),
                ),
            )]

        elif name == "investigate-cluster":
            cluster_id = args.get("cluster_id", "0")
            return [PromptMessage(
                role="user",
                content=TextContent(
                    type="text",
                    text=(
                        f"Investigate cluster {cluster_id} in detail:\n\n"
                        f"1. Call `get_cluster` with cluster_id={cluster_id} to see all member records.\n"
                        f"2. Call `agent_explain_cluster` with cluster_id={cluster_id} to understand why these records were grouped.\n"
                        f"3. Call `get_golden_record` with cluster_id={cluster_id} to see the merged canonical record.\n"
                        "4. For any pair that looks suspicious, call `explain_match` with the two records.\n"
                        "5. If a record doesn't belong, call `unmerge_record` to remove it.\n"
                        "6. If the whole cluster is wrong, call `shatter_cluster` to break it apart.\n\n"
                        "Start with step 1 now."
                    ),
                ),
            )]

        elif name == "compare-records":
            rec_a = args.get("record_a", "{}")
            rec_b = args.get("record_b", "{}")
            return [PromptMessage(
                role="user",
                content=TextContent(
                    type="text",
                    text=(
                        "Compare these two records and tell me if they're the same entity:\n\n"
                        f"Record A: {rec_a}\n"
                        f"Record B: {rec_b}\n\n"
                        "1. Call `explain_match` with these two records to get the field-by-field score breakdown.\n"
                        "2. Interpret the result: which fields agree, which disagree, what's the overall score vs threshold.\n"
                        "3. Give a clear verdict: match or no-match, and explain why in plain English."
                    ),
                ),
            )]

        elif name == "data-quality-audit":
            return [PromptMessage(
                role="user",
                content=TextContent(
                    type="text",
                    text=(
                        "Run a full data quality audit on the loaded dataset:\n\n"
                        "1. Call `profile_data` to get column types, null rates, unique counts, and samples.\n"
                        "2. If goldencheck is available, call `scan_quality` for deeper issue detection.\n"
                        "3. Summarize: which columns have quality issues (high nulls, low cardinality, inconsistent formats)?\n"
                        "4. Recommend specific cleaning steps before running entity resolution.\n"
                        "5. If goldenflow is available, suggest `run_transforms` for phone/date/unicode normalization."
                    ),
                ),
            )]

        elif name == "pprl-setup":
            level = args.get("security_level", "high")
            return [PromptMessage(
                role="user",
                content=TextContent(
                    type="text",
                    text=(
                        f"Help me set up privacy-preserving record linkage at '{level}' security:\n\n"
                        "1. Call `suggest_pprl` to check if my data needs privacy-preserving matching.\n"
                        "2. Call `pprl_auto_config` to get recommended PPRL settings (bloom filter params, fields, thresholds).\n"
                        "3. Review the config and explain the tradeoffs (precision vs recall vs privacy).\n"
                        "4. When ready, call `pprl_link` to execute the privacy-preserving linkage.\n"
                        "5. Show me the results and compare accuracy to non-PPRL matching if available."
                    ),
                ),
            )]

        return [PromptMessage(
            role="user",
            content=TextContent(type="text", text=f"Unknown prompt: {name}"),
        )]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        from goldenmatch.mcp._session_ctx import (
            reset_current_session_id,
            session_key_from_context,
            set_current_session_id,
        )
        _tok = set_current_session_id(session_key_from_context(server))
        try:
            name = _resolve_alias(name)
            # Anonymous, opt-in usage event: the TOOL NAME only -- never `arguments`
            # (which can carry user data). No-op unless GOLDENMATCH_ANALYTICS=1.
            try:
                from goldenmatch.core.analytics import capture
                capture("mcp_tool_call", {"surface": "mcp", "tool": name})
            except Exception:  # noqa: BLE001 - analytics is never load-bearing
                pass
            # Delegate agent-level tools to the agent handler
            if name in _AGENT_TOOL_NAMES:
                return handle_agent_tool(name, arguments)
            if name in _MEMORY_TOOL_NAMES:
                return handle_memory_tool(name, arguments)
            if name in IDENTITY_TOOL_NAMES:
                return await handle_identity_tool(name, arguments)
            try:
                if name in DOCUMENT_TOOL_NAMES:
                    result = handle_document_tool(name, arguments)
                elif name in ROUTING_TOOL_NAMES:
                    result = handle_routing_tool(name, arguments)
                else:
                    result = _handle_tool(name, arguments)
                return [TextContent(type="text", text=json.dumps(result, default=str, indent=2))]
            except Exception as e:
                return [TextContent(type="text", text=json.dumps({"error": str(e)}))]
        finally:
            reset_current_session_id(_tok)

    return server


@dataclass
class _RunState:
    result: object | None
    config: object | None
    data: object | None
    rows: list
    id_to_idx: dict


def _resolve_run_state() -> _RunState:
    """Active run state: module globals when set (standalone --file, byte-
    identical), else the current MCP session's AgentSession (aggregator path),
    else all-None (callers return a clean 'no run loaded' error).

    The session path returns a __row_id__-augmented frame as `data` (raw
    AgentSession.data lacks it; match_one requires it), built once and cached on
    the session as _mcp_data/_mcp_rows/_mcp_id_to_idx."""
    if _result is not None or _config is not None or _engine is not None:
        return _RunState(
            result=_result,
            config=_config,
            data=_engine.data if _engine is not None else None,
            rows=_rows,
            id_to_idx=_id_to_idx,
        )
    from goldenmatch.mcp._session_ctx import current_session_id
    from goldenmatch.mcp._session_store import _STORE
    sid = current_session_id()
    sess = _STORE.get(sid) if sid else None
    if sess is None or getattr(sess, "result", None) is None:
        return _RunState(None, None, None, [], {})
    raw = sess.data
    if not hasattr(sess, "_mcp_data") or sess._mcp_src is not raw:
        df = raw
        if df is not None and "__row_id__" not in df.columns:
            df = df.with_row_index("__row_id__")
        sess._mcp_src = raw
        sess._mcp_data = df
        rows = df.to_dicts() if df is not None else []
        sess._mcp_rows = rows
        sess._mcp_id_to_idx = {r["__row_id__"]: i for i, r in enumerate(rows)}
    return _RunState(sess.result, sess.config, sess._mcp_data,
                     sess._mcp_rows, sess._mcp_id_to_idx)


def _handle_tool(name: str, args: dict) -> dict:
    """Dispatch tool calls."""
    from goldenmatch.mcp import _ingest
    _ingest_err = _ingest.resolve_ingest_args(name, args)
    if _ingest_err is not None:
        return _ingest_err
    if name == "get_stats":
        return _tool_get_stats()
    elif name == "find_duplicates":
        return _tool_find_duplicates(args.get("record", {}), args.get("top_k", 5))
    elif name == "explain_match":
        return _tool_explain_match(args.get("record_a", {}), args.get("record_b", {}))
    elif name == "list_clusters":
        return _tool_list_clusters(args.get("min_size", 2), args.get("limit", 20))
    elif name == "get_cluster":
        return _tool_get_cluster(args["cluster_id"])
    elif name == "get_golden_record":
        return _tool_get_golden_record(args["cluster_id"])
    elif name == "match_record":
        return _tool_match_record(args.get("record", {}), args.get("threshold"), args.get("top_k", 5))
    elif name == "unmerge_record":
        return _tool_unmerge_record(args["record_id"])
    elif name == "shatter_cluster":
        return _tool_shatter_cluster(args["cluster_id"])
    elif name == "suggest_config":
        return _tool_suggest_config(args.get("bad_merges", []))
    elif name == "review_config":
        return _tool_review_config()
    elif name == "convert_splink_config":
        return _tool_convert_splink_config(args.get("settings_json", ""), args.get("strict", False))
    elif name == "profile_data":
        return _tool_profile_data()
    elif name == "export_results":
        return _tool_export_results(args["output_path"], args.get("format", "csv"))
    elif name == "list_domains":
        return _tool_list_domains()
    elif name == "score_strings":
        return _tool_score_strings(
            str(args.get("a", "")),
            str(args.get("b", "")),
            args.get("scorer") or "jaro_winkler",
        )
    elif name == "score_pair":
        return _tool_score_pair(
            args.get("row_a") or {},
            args.get("row_b") or {},
            args.get("fields"),
        )
    elif name == "find_exact_matches":
        return _tool_find_exact_matches(
            args["path"], args["field"], args.get("transforms")
        )
    elif name == "find_fuzzy_matches":
        return _tool_find_fuzzy_matches(
            args["path"],
            args["field"],
            args.get("scorer") or "jaro_winkler",
            args.get("threshold"),
            args.get("transforms"),
        )
    elif name == "build_clusters":
        return _tool_build_clusters(
            args["path"],
            args.get("exact"),
            args.get("fuzzy"),
            args.get("blocking"),
            args.get("threshold"),
        )
    elif name == "server_info":
        return _tool_server_info()
    elif name == "read_file":
        return _tool_read_file(args["path"], args.get("limit"))
    elif name == "write_csv":
        return _tool_write_csv(args["path"], args.get("rows"))
    elif name == "list_scorers":
        return _tool_list_scorers()
    elif name == "list_transforms":
        return _tool_list_transforms()
    elif name == "list_strategies":
        return _tool_list_strategies()
    elif name == "list_blocking_strategies":
        return _tool_list_blocking_strategies()
    elif name == "create_domain":
        return _tool_create_domain(args)
    elif name == "test_domain":
        return _tool_test_domain(args.get("domain_name", ""), args.get("sample_size", 10))
    elif name == "pprl_auto_config":
        return _tool_pprl_auto_config(args.get("security_level", "high"), args.get("use_llm", False))
    elif name == "pprl_link":
        return _tool_pprl_link(args)
    elif name == "evaluate":
        return _tool_evaluate(
            args["ground_truth_path"],
            args.get("col_a", "id_a"),
            args.get("col_b", "id_b"),
        )
    elif name == "analyze_blocking":
        return _tool_analyze_blocking(
            args.get("sample_size", 1000),
            args.get("target_block_size", 5000),
            args.get("limit", 10),
        )
    elif name == "compare_clusters":
        return _tool_compare_clusters(args["clusters_a_path"], args["clusters_b_path"])
    elif name == "schema_match":
        return _tool_schema_match(args["file_a"], args["file_b"], args.get("min_score", 0.5))
    elif name == "lineage":
        return _tool_lineage(
            args.get("max_pairs", 100),
            args.get("natural_language", False),
            args.get("output_dir"),
        )
    elif name == "list_runs":
        return _tool_list_runs(args.get("output_dir", "."))
    elif name == "rollback":
        return _tool_rollback(args["run_id"], args.get("output_dir", "."))
    elif name == "config_weaknesses":
        return _tool_config_weaknesses(
            args.get("max_findings", 6), args.get("phrasing", "plain")
        )
    else:
        return {"error": f"Unknown tool: {name}"}


def _tool_get_stats() -> dict:
    s = _result.stats
    return {
        "total_records": s.total_records,
        "total_clusters": s.total_clusters,
        "singleton_count": s.singleton_count,
        "match_rate": round(s.match_rate, 2),
        "avg_cluster_size": round(s.avg_cluster_size, 2),
        "max_cluster_size": s.max_cluster_size,
        "total_pairs": len(_result.scored_pairs),
    }


def _tool_find_duplicates(record: dict, top_k: int) -> dict:
    from goldenmatch.core.explainer import explain_pair

    rs = _resolve_run_state()
    if rs.config is None:
        return {"error": "No run loaded. Run agent_deduplicate (or dedupe_file) in this session first."}
    matchkeys = rs.config.get_matchkeys()
    results = []

    for mk in matchkeys:
        if mk.type != "weighted":
            continue
        for row in rs.rows:
            exp = explain_pair(record, row, mk.fields, mk.threshold or 0.80)
            if exp.is_match:
                clean = {k: v for k, v in row.items() if not k.startswith("__")}
                results.append({
                    "record": clean,
                    "score": round(exp.total_score, 4),
                    "top_contributor": exp.top_contributor,
                })

    results.sort(key=lambda x: -x["score"])
    return {"matches": results[:top_k], "count": min(len(results), top_k)}


def _tool_explain_match(record_a: dict, record_b: dict) -> dict:
    from goldenmatch.core.explainer import explain_pair

    rs = _resolve_run_state()
    if rs.config is None:
        return {"error": "No run loaded. Run agent_deduplicate (or dedupe_file) in this session first."}
    matchkeys = rs.config.get_matchkeys()
    fields = []
    threshold = 0.80
    for mk in matchkeys:
        if mk.type == "weighted":
            fields = mk.fields
            threshold = mk.threshold or 0.80
            break

    exp = explain_pair(record_a, record_b, fields, threshold)
    return {
        "total_score": round(exp.total_score, 4),
        "threshold": exp.threshold,
        "is_match": exp.is_match,
        "top_contributor": exp.top_contributor,
        "weakest_field": exp.weakest_field,
        "fields": [
            {
                "field": f.field_name,
                "scorer": f.scorer,
                "value_a": f.value_a,
                "value_b": f.value_b,
                "score": round(f.score, 4),
                "weight": f.weight,
                "contribution": round(f.contribution, 4),
                "diff_type": f.diff_type,
            }
            for f in exp.fields
        ],
    }


def _tool_list_clusters(min_size: int, limit: int) -> dict:
    rs = _resolve_run_state()
    if rs.result is None:
        return {"error": "No run loaded. Run agent_deduplicate (or dedupe_file) in this session first."}
    result_clusters = getattr(rs.result, "clusters", None)
    if result_clusters is None:
        return {"error": "No clusters on this run (a match_sources run has matched/unmatched records, not clusters)."}
    clusters = []
    for cid, info in result_clusters.items():
        if info["size"] >= min_size:
            clusters.append({
                "cluster_id": cid,
                "size": info["size"],
                "oversized": info.get("oversized", False),
            })
    clusters.sort(key=lambda x: -x["size"])
    return {"clusters": clusters[:limit], "total": len(clusters)}


def _tool_get_cluster(cluster_id: int) -> dict:
    rs = _resolve_run_state()
    if rs.result is None:
        return {"error": "No run loaded. Run agent_deduplicate (or dedupe_file) in this session first."}
    result_clusters = getattr(rs.result, "clusters", None)
    if result_clusters is None:
        return {"error": "No clusters on this run (a match_sources run has matched/unmatched records, not clusters)."}
    info = result_clusters.get(cluster_id)
    if not info:
        return {"error": f"Cluster {cluster_id} not found"}

    members = []
    for mid in info["members"]:
        idx = rs.id_to_idx.get(mid)
        if idx is not None:
            clean = {k: v for k, v in rs.rows[idx].items() if not k.startswith("__")}
            members.append(clean)

    return {"cluster_id": cluster_id, "size": info["size"], "members": members}


def _golden_as_table(golden):
    """Normalize a golden frame to a ``pyarrow.Table``.

    Session runs (``dedupe_df`` -> ``DedupeResult.golden``) already produce a
    ``pa.Table``, but the standalone ``MatchEngine.run_full`` path still yields a
    polars ``DataFrame`` (``EngineResult.golden: pl.DataFrame``). The
    golden-reading MCP tools are written against the ``pa.Table`` API
    (``to_pylist``/``column_names``/``select``/``num_rows``/``filter``), so a
    polars golden would ``AttributeError``. Convert here so both paths work. The
    lasting fix is making ``EngineResult.golden`` a ``pa.Table`` in the
    polars-eviction program; this keeps the MCP surface working meanwhile.
    """
    if golden is None:
        return None
    import pyarrow as pa
    if isinstance(golden, pa.Table):
        return golden
    to_arrow = getattr(golden, "to_arrow", None)  # polars.DataFrame -> pa.Table
    return to_arrow() if callable(to_arrow) else golden


def _tool_get_golden_record(cluster_id: int) -> dict:
    rs = _resolve_run_state()
    if rs.result is None:
        return {"error": "No run loaded. Run agent_deduplicate (or dedupe_file) in this session first."}
    golden = _golden_as_table(getattr(rs.result, "golden", None))
    if golden is None:
        return {"error": "No golden records available"}

    import pyarrow.compute as pc

    golden_rows = golden.filter(
        pc.equal(golden.column("__cluster_id__"), cluster_id)
    ) if "__cluster_id__" in golden.column_names else None

    if golden_rows is None or golden_rows.num_rows == 0:
        return {"error": f"No golden record for cluster {cluster_id}"}

    row = golden_rows.to_pylist()[0]
    clean = {k: v for k, v in row.items() if not k.startswith("__")}
    return {"cluster_id": cluster_id, "golden_record": clean}


def _tool_match_record(record: dict, threshold: float | None, top_k: int) -> dict:
    """Match a single record against the dataset using match_one."""
    from goldenmatch.core.match_one import match_one

    rs = _resolve_run_state()
    if rs.config is None or rs.data is None:
        return {"error": "No run loaded. Run agent_deduplicate (or dedupe_file) in this session first."}
    matchkeys = rs.config.get_matchkeys()
    all_matches = []

    for mk in matchkeys:
        if mk.type != "weighted":
            continue
        t = threshold if threshold is not None else (mk.threshold or 0.80)
        # Temporarily override threshold if user specified one
        import copy
        mk_copy = copy.deepcopy(mk)
        mk_copy.threshold = t

        matches = match_one(record, rs.data, mk_copy)
        for row_id, score in matches:
            idx = rs.id_to_idx.get(row_id)
            if idx is not None:
                clean = {k: v for k, v in rs.rows[idx].items() if not k.startswith("__")}
                all_matches.append({
                    "row_id": row_id,
                    "score": round(score, 4),
                    "record": clean,
                })

    # Deduplicate by row_id, keep highest score
    seen = {}
    for m in all_matches:
        rid = m["row_id"]
        if rid not in seen or m["score"] > seen[rid]["score"]:
            seen[rid] = m
    deduped = sorted(seen.values(), key=lambda x: -x["score"])[:top_k]

    return {
        "matches": deduped,
        "count": len(deduped),
        "input_record": record,
    }


def _tool_unmerge_record(record_id: int) -> dict:
    """Remove a record from its cluster."""
    global _result

    updated = _engine.unmerge_record(record_id)
    if updated is None:
        return {"error": "No matching results. Run matching first."}

    _result = updated

    # Find the record's new cluster
    for cid, info in _result.clusters.items():
        if record_id in info["members"]:
            return {
                "status": "unmerged",
                "record_id": record_id,
                "new_cluster_id": cid,
                "new_cluster_size": info["size"],
                "total_clusters": _result.stats.total_clusters,
            }

    return {"status": "unmerged", "record_id": record_id}


def _tool_shatter_cluster(cluster_id: int) -> dict:
    """Break a cluster into singletons."""
    global _result

    info = _result.clusters.get(cluster_id)
    if info is None:
        return {"error": f"Cluster {cluster_id} not found"}

    member_count = info["size"]
    updated = _engine.unmerge_cluster(cluster_id)
    if updated is None:
        return {"error": "No matching results. Run matching first."}

    _result = updated

    return {
        "status": "shattered",
        "cluster_id": cluster_id,
        "records_freed": member_count,
        "total_clusters": _result.stats.total_clusters,
    }


def _tool_suggest_config(bad_merges: list[dict]) -> dict:
    """Analyze bad merges and suggest config changes."""
    from goldenmatch.core.explainer import explain_pair

    if not bad_merges:
        return {"error": "Provide at least one bad merge example."}

    matchkeys = _config.get_matchkeys()
    fields = []
    threshold = 0.80
    for mk in matchkeys:
        if mk.type == "weighted":
            fields = mk.fields
            threshold = mk.threshold or 0.80
            break

    # Analyze each bad merge
    analyses = []
    field_scores: dict[str, list[float]] = {}

    for merge in bad_merges:
        rec_a = merge.get("record_a", {})
        rec_b = merge.get("record_b", {})
        reason = merge.get("reason", "")

        exp = explain_pair(rec_a, rec_b, fields, threshold)

        analysis = {
            "total_score": round(exp.total_score, 4),
            "is_match": exp.is_match,
            "reason": reason,
            "guilty_fields": [],
        }

        for f in exp.fields:
            if f.score >= 0.7:  # This field contributed to the bad merge
                analysis["guilty_fields"].append({
                    "field": f.field_name,
                    "scorer": f.scorer,
                    "score": round(f.score, 4),
                    "value_a": f.value_a,
                    "value_b": f.value_b,
                })
            field_scores.setdefault(f.field_name, []).append(f.score)

        analyses.append(analysis)

    # Generate suggestions
    suggestions = []

    # Suggest raising threshold if bad merges have scores close to current threshold
    bad_scores = [a["total_score"] for a in analyses if a["is_match"]]
    if bad_scores:
        max_bad = max(bad_scores)
        if max_bad < 1.0:
            suggested_threshold = round(max_bad + 0.05, 2)
            suggestions.append({
                "type": "raise_threshold",
                "current": threshold,
                "suggested": suggested_threshold,
                "reason": f"Bad merges have scores up to {max_bad:.2f}. "
                         f"Raising threshold to {suggested_threshold} would reject them.",
            })

    # Identify fields that are too permissive
    for field_name, scores in field_scores.items():
        avg = sum(scores) / len(scores)
        if avg >= 0.7:
            suggestions.append({
                "type": "reduce_field_weight",
                "field": field_name,
                "avg_score_on_bad_merges": round(avg, 3),
                "reason": f"Field '{field_name}' scores high ({avg:.2f}) on bad merges. "
                         f"Consider reducing its weight or switching to a stricter scorer.",
            })

    return {
        "analyses": analyses,
        "suggestions": suggestions,
        "current_threshold": threshold,
        "bad_merges_analyzed": len(analyses),
    }


def _tool_convert_splink_config(settings_json: str, strict: bool = False) -> dict:
    """Convert an inline Splink settings JSON string into a GoldenMatch config.

    The remote MCP surface takes content inline (no filesystem assumptions for
    the caller): settings arrive as a JSON string, results return inline --
    the config as YAML text, the findings report, a summary, and (when the
    Splink input was trained) the EMResult as a dict so remote callers can
    persist it themselves. Errors use the server's clean-dict convention
    (`{"error": ...}`) rather than letting exceptions cross the MCP boundary.
    """
    import yaml

    from goldenmatch.config.from_splink import SplinkConversionError, from_splink

    try:
        settings = json.loads(settings_json)
    except (json.JSONDecodeError, TypeError) as exc:
        return {"error": f"settings_json is not valid JSON: {exc}"}

    if not isinstance(settings, dict):
        return {
            "error": (
                "settings_json must decode to a JSON object (Splink settings "
                f"dict), got {type(settings).__name__}"
            )
        }

    try:
        conversion = from_splink(settings, strict=strict)
    except SplinkConversionError as exc:
        return {"error": str(exc)}

    dumped = conversion.config.model_dump(exclude_none=True, exclude_defaults=True)
    config_yaml = yaml.safe_dump(dumped, sort_keys=False)

    findings = [
        {
            "severity": f.severity,
            "splink_path": f.splink_path,
            "message": f.message,
            "mapped_to": f.mapped_to,
        }
        for f in conversion.report.findings
    ]

    em_model = conversion.em_model.to_dict() if conversion.em_model is not None else None

    usage_note = (
        "Save config_yaml to a file and load it as the GoldenMatch config. "
        + (
            "This model carries trained m/u probabilities: save em_model as "
            "JSON and set matchkeys[0].model_path to that file's path so "
            "GoldenMatch reuses it instead of re-training via EM."
            if em_model is not None
            else "No trained model was carried by this input; GoldenMatch will "
            "train via EM on first run."
        )
    )

    return {
        "config_yaml": config_yaml,
        "findings": findings,
        "summary": conversion.report.summary(),
        "em_model": em_model,
        "usage_note": usage_note,
    }


def _tool_review_config() -> dict:
    """Run the config healer over the loaded dataset and return ranked,
    self-verified suggestions in the shared cross-surface wire shape.

    Fail-safe: returns a structured ``native_required`` payload when the native
    kernel is absent, and an ``error`` payload on any other failure -- never
    raises out of the MCP dispatch.
    """
    if _engine is None or _config is None:
        return {"error": "No dataset loaded. Start the server with --file."}

    from goldenmatch.core.suggest import SuggestionsNativeRequired, review_config
    from goldenmatch.core.suggest.surface import serialize_suggestions

    try:
        suggestions = review_config(_engine.data, _config)
    except SuggestionsNativeRequired as exc:
        return {"suggestions": [], "native_required": True, "message": str(exc)}
    except Exception as exc:  # noqa: BLE001 - fail-safe MCP handler
        return {"error": f"review_config failed: {exc}"}

    return {"suggestions": serialize_suggestions(suggestions, verified=True)}


def _tool_profile_data() -> dict:
    profile = _engine.profile
    cols = []
    col_list = profile.get("columns", [])
    if isinstance(col_list, list):
        for info in col_list:
            if not isinstance(info, dict):
                continue
            cols.append({
                "column": info.get("name", ""),
                "type": info.get("suspected_type", info.get("dtype", "unknown")),
                "null_rate": round(info.get("null_rate", 0) * 100, 1),
                "unique_rate": round(info.get("unique_rate", 0) * 100, 1),
                "sample": [str(v) for v in info.get("sample_values", [])[:3]],
            })
    return {"columns": cols, "total_records": _engine.row_count}


def _safe_path_or_error(value: str) -> Path | dict:
    """Validate *value* via safe_path; return the resolved Path or an error dict."""
    try:
        return safe_path(value)
    except (ValueError, PathOutsideAllowedRootError) as exc:
        return {"error": str(exc)}


def _tool_export_results(output_path: str, fmt: str) -> dict:
    rs = _resolve_run_state()
    if rs.result is None:
        return {"error": "No run loaded. Run agent_deduplicate (or dedupe_file) in this session first."}
    path = _safe_path_or_error(output_path)
    if isinstance(path, dict):
        return path
    golden = _golden_as_table(getattr(rs.result, "golden", None))
    if fmt == "json":
        if golden is not None:
            golden_dicts = golden.to_pylist()
            clean = [{k: v for k, v in r.items() if not k.startswith("__")} for r in golden_dicts]
            path.write_text(json.dumps(clean, default=str, indent=2))
        else:
            path.write_text("[]")
    else:
        if golden is not None:
            from pyarrow import csv as _pacsv

            cols = [c for c in golden.column_names if not c.startswith("__")]
            _pacsv.write_csv(golden.select(cols), str(path))
        else:
            path.write_text("")

    return {"exported": str(path), "format": fmt, "records": golden.num_rows if golden is not None else 0}


def _tool_list_domains() -> dict:
    """List available domain extraction rulebooks."""
    from goldenmatch.core.domain_registry import discover_rulebooks
    rulebooks = discover_rulebooks()
    result = []
    for name, rb in rulebooks.items():
        result.append({
            "name": rb.name,
            "signals": rb.signals,
            "identifier_patterns": list(rb.identifier_patterns.keys()),
            "brand_count": len(rb.brand_patterns),
            "attribute_patterns": list(rb.attribute_patterns.keys()),
        })
    return {"domains": result, "count": len(result)}


# ---------------------------------------------------------------------------
# Host helpers (reverse-parity with the TS MCP surface)
#
# These are NOT entity-resolution capabilities -- they let an agent driving this
# server stage an input and collect an output without a second toolchain.
#
# CONTAINMENT, STATED EXACTLY (verified, not assumed): both tools route through
# `core._paths.safe_path`, which rejects NUL bytes and resolves the path, but
# enforces CONTAINMENT ONLY WHEN `GOLDENMATCH_ALLOWED_ROOT` IS SET. With that env
# var configured, traversal (`../`) and absolute escapes are blocked for read AND
# write. With it UNSET -- the default -- these tools can reach any path the server
# process can, exactly like the pre-existing `upload_dataset` / `export_results`
# and the find_*/build_clusters primitives, all of which use the same guard. So
# this adds no new reach beyond the surface that already existed; an operator
# exposing this server to untrusted callers should set `GOLDENMATCH_ALLOWED_ROOT`
# (and `GOLDENMATCH_MCP_TOKEN`, which the HTTP transport already fails closed on).
#
# `write_csv` additionally refuses anything that is not a list of objects, so a
# scalar or bare string cannot be coerced into a surprise file write.
# ---------------------------------------------------------------------------

_READ_FILE_DEFAULT_LIMIT = 100


def _tool_server_info() -> dict:
    """Server metadata (mirrors TS `server_info`).

    `tool_count` is DERIVED from the live TOOLS list, never a literal -- the TS
    side does the same, so the number cannot drift from the real surface.
    """
    from goldenmatch import __version__

    return {
        "name": "goldenmatch",
        "version": __version__,
        "tool_count": len(TOOLS),
        "description": "GoldenMatch MCP server (stdio + streamable HTTP)",
    }


def _tool_read_file(path: str, limit: object) -> dict:
    """Read a file and return the first N records (mirrors TS -> `{total, returned, rows}`)."""
    p = _safe_path_or_error(path)
    if isinstance(p, dict):
        return p
    try:
        n = _READ_FILE_DEFAULT_LIMIT if limit is None else max(0, int(limit))
    except (TypeError, ValueError):
        return {"error": f"`limit` must be a number, got {limit!r}"}

    try:
        from goldenmatch.core.ingest import load_file

        rows = load_file(str(p)).collect().to_dicts()
    except (FileNotFoundError, OSError) as exc:
        return {"error": f"Could not read {path}: {exc}"}
    except Exception as exc:  # noqa: BLE001 - malformed file is a tool error, not a crash
        return {"error": f"Could not parse {path}: {exc}"}

    return {"total": len(rows), "returned": min(len(rows), n), "rows": rows[:n]}


def _tool_write_csv(path: str, rows: object) -> dict:
    """Write record objects to a CSV (mirrors TS -> `{written, path}`)."""
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        return {"error": "`rows` must be an array of objects."}

    p = _safe_path_or_error(path)
    if isinstance(p, dict):
        return p

    try:
        import polars as pl

        # An empty list has no schema to infer; write a header-less empty file
        # rather than raising, so a zero-result export still produces the file.
        (pl.DataFrame(rows) if rows else pl.DataFrame()).write_csv(str(p))
    except OSError as exc:
        return {"error": f"Could not write {path}: {exc}"}
    except Exception as exc:  # noqa: BLE001 - surface as a tool error
        return {"error": f"Could not serialize rows to CSV: {exc}"}

    return {"written": len(rows), "path": str(p)}


# ---------------------------------------------------------------------------
# Core primitives (reverse-parity with the TS MCP surface)
#
# These wrap functions goldenmatch already exposed in-process; the gap was that
# an AGENT could not reach them over MCP without loading a run into session
# state first. All five are STATELESS -- they never read or write _resolve_run_state.
# Response shapes mirror the TS handlers exactly (including the 100-pair /
# 200-cluster response caps, which keep a big file from flooding the wire).
# ---------------------------------------------------------------------------

_PAIR_RESPONSE_CAP = 100
_CLUSTER_RESPONSE_CAP = 200
_DEFAULT_TRANSFORMS = ["lowercase", "strip"]


def _tool_score_strings(a: str, b: str, scorer: str) -> dict:
    """Score two strings (mirrors TS `score_strings` -> `{scorer, score}`)."""
    from goldenmatch._api import score_strings

    try:
        return {"scorer": scorer, "score": score_strings(a, b, scorer)}
    except (ValueError, KeyError) as exc:
        return {"error": f"Unknown or unusable scorer {scorer!r}: {exc}"}


def _tool_score_pair(row_a: dict, row_b: dict, fields: list | None) -> dict:
    """Score two records across weighted fields.

    Mirrors TS `score_pair` -> `{score, field_count}`, over the SAME primitive
    (`core.scorer.score_pair`, whose signature already matches TS `scorePair`).
    Per-field defaults match the TS `buildFieldsFromArg` helper.
    """
    from goldenmatch.config.schemas import MatchkeyField
    from goldenmatch.core.scorer import score_pair

    if not fields:
        return {"error": "`fields` must be a non-empty list of {field, scorer, weight, transforms}."}
    try:
        mk_fields = [
            MatchkeyField(
                field=f["field"],
                scorer=f.get("scorer") or "jaro_winkler",
                weight=float(f.get("weight", 1.0)),
                transforms=list(f.get("transforms") or []),
            )
            for f in fields
        ]
    except (KeyError, TypeError, ValueError) as exc:
        return {"error": f"Invalid `fields` entry: {exc}"}
    return {"score": score_pair(row_a, row_b, mk_fields), "field_count": len(mk_fields)}


def _adhoc_matchkey(
    name: str,
    mk_type: str,
    field: str,
    scorer: str,
    transforms: list | None,
    threshold: float | None,
):
    """Build the one-field matchkey the two find_* tools score with.

    `mk_type` matters: TS uses `exact` for find_exact_matches and `weighted` for
    find_fuzzy_matches, and Pydantic REQUIRES a threshold on a weighted matchkey
    (an exact one takes none) -- so the two cannot share a single type.
    """
    from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField

    return MatchkeyConfig(
        name=name,
        type=mk_type,
        fields=[
            MatchkeyField(
                field=field,
                scorer=scorer,
                weight=1.0,
                transforms=list(transforms) if transforms is not None else list(_DEFAULT_TRANSFORMS),
            )
        ],
        threshold=threshold,
    )


def _load_rows_with_ids(path: str):
    """Load a file and stamp `__row_id__` (the ids the returned pairs refer to)."""
    import polars as pl

    from goldenmatch.core.ingest import load_file

    df = load_file(path).collect()
    if "__row_id__" not in df.columns:
        df = df.with_row_index("__row_id__").with_columns(pl.col("__row_id__").cast(pl.Int64))
    return df


def _tool_find_exact_matches(path: str, field: str, transforms: list | None) -> dict:
    """Exact-match pairs on one field (mirrors TS -> `{pair_count, pairs}`)."""
    from goldenmatch.core.scorer import find_exact_matches

    p = _safe_path_or_error(path)
    if isinstance(p, dict):
        return p
    try:
        df = _load_rows_with_ids(str(p))
    except (FileNotFoundError, OSError) as exc:
        return {"error": f"Could not read {path}: {exc}"}
    if field not in df.columns:
        return {"error": f"Column {field!r} not in {path} (have: {sorted(df.columns)[:20]})"}

    # find_exact_matches reads the PRECOMPUTED `__mk_<name>__` column, so the
    # matchkey column has to be materialized first (the pipeline does this in
    # its own prep step). Without it: ColumnNotFoundError, not an empty result.
    from goldenmatch.core.matchkey import compute_matchkeys

    mk = _adhoc_matchkey("adhoc_exact", "exact", field, "exact", transforms, None)
    pairs = find_exact_matches(compute_matchkeys(df.lazy(), [mk]), mk)
    return {
        "pair_count": len(pairs),
        "pairs": [[a, b, s] for a, b, s in pairs[:_PAIR_RESPONSE_CAP]],
    }


def _tool_find_fuzzy_matches(
    path: str, field: str, scorer: str, threshold: float | None, transforms: list | None
) -> dict:
    """Fuzzy-scored pairs on one field (mirrors TS -> `{pair_count, pairs}`)."""
    from goldenmatch.core.scorer import find_fuzzy_matches

    p = _safe_path_or_error(path)
    if isinstance(p, dict):
        return p
    try:
        df = _load_rows_with_ids(str(p))
    except (FileNotFoundError, OSError) as exc:
        return {"error": f"Could not read {path}: {exc}"}
    if field not in df.columns:
        return {"error": f"Column {field!r} not in {path} (have: {sorted(df.columns)[:20]})"}

    mk = _adhoc_matchkey(
        "adhoc_fuzzy",
        "weighted",
        field,
        scorer,
        transforms,
        0.85 if threshold is None else float(threshold),
    )
    pairs = find_fuzzy_matches(df, mk)
    return {
        "pair_count": len(pairs),
        "pairs": [[a, b, s] for a, b, s in pairs[:_PAIR_RESPONSE_CAP]],
    }


def _tool_build_clusters(
    path: str,
    exact: list | None,
    fuzzy: dict | None,
    blocking: list | None,
    threshold: float | None,
) -> dict:
    """Cluster a file (mirrors TS -> `{cluster_count, clusters}`).

    NOTE the tool name is about the OUTCOME, not the raw `core.cluster.build_clusters`
    primitive: TS runs a full `dedupe()` and reports the resulting clusters, so this
    runs `dedupe_df` for the same semantics rather than clustering caller-supplied pairs.
    """
    from goldenmatch import dedupe_df

    p = _safe_path_or_error(path)
    if isinstance(p, dict):
        return p
    try:
        df = _load_rows_with_ids(str(p))
    except (FileNotFoundError, OSError) as exc:
        return {"error": f"Could not read {path}: {exc}"}

    kwargs: dict = {}
    if exact:
        kwargs["exact"] = list(exact)
    if fuzzy:
        kwargs["fuzzy"] = dict(fuzzy)
    if blocking:
        kwargs["blocking"] = list(blocking)
    if threshold is not None:
        kwargs["threshold"] = float(threshold)

    try:
        result = dedupe_df(df, **kwargs)
    except Exception as exc:  # noqa: BLE001 - surface as a tool error, never a crash
        return {"error": f"Clustering failed: {exc}"}

    clusters = [
        {
            "cluster_id": cid,
            "size": info.get("size", len(info.get("members", []))),
            "confidence": info.get("confidence"),
            "quality": info.get("cluster_quality"),
            "members": list(info.get("members", [])),
        }
        for cid, info in (result.clusters or {}).items()
    ]
    return {
        "cluster_count": len(clusters),
        "clusters": clusters[:_CLUSTER_RESPONSE_CAP],
    }


def _tool_list_scorers() -> dict:
    """List all available similarity scorers (parity with the TS MCP tool).

    Sorted for a deterministic wire order. Mirrors the TS `list_scorers`
    (`{ scorers: [...VALID_SCORERS] }`)."""
    from goldenmatch.config.schemas import VALID_SCORERS

    scorers = sorted(VALID_SCORERS)
    return {"scorers": scorers, "count": len(scorers)}


def _tool_list_transforms() -> dict:
    """List all available field transforms (parity with the TS MCP tool)."""
    from goldenmatch.config.schemas import VALID_SIMPLE_TRANSFORMS

    transforms = sorted(VALID_SIMPLE_TRANSFORMS)
    return {"transforms": transforms, "count": len(transforms)}


def _tool_list_strategies() -> dict:
    """List all golden-record survivorship strategies (parity with the TS MCP
    tool). Same concept as TS `VALID_STRATEGIES` (survivorship, not blocking)."""
    from goldenmatch.config.schemas import VALID_STRATEGIES

    strategies = sorted(VALID_STRATEGIES)
    return {"strategies": strategies, "count": len(strategies)}


def _tool_list_blocking_strategies() -> dict:
    """List all blocking-strategy names (parity with the TS MCP tool).

    Derived from ``BlockingConfig.strategy`` (the same source the ``api_parity``
    gate's ``blocking_strategies`` surface reads), so the Python set stays in
    lockstep with the schema — it includes the Python-only ``lsh`` / ``simhash``
    / ``perceptual`` strategies the TS port lacks (declared in
    ``parity/goldenmatch.yaml``). Mirrors the TS ``list_blocking_strategies``
    (`{ strategies: [...] }`)."""
    from typing import get_args

    from goldenmatch.config.schemas import BlockingConfig

    strategies = sorted(get_args(BlockingConfig.model_fields["strategy"].annotation))
    return {"strategies": strategies, "count": len(strategies)}


def _tool_create_domain(args: dict) -> dict:
    """Create a custom domain extraction rulebook."""
    from pathlib import Path

    from goldenmatch.core.domain_registry import DomainRulebook, save_rulebook

    name = args["name"]
    scope = args.get("scope", "local")

    if scope == "global":
        save_dir = Path.home() / ".goldenmatch" / "domains"
    else:
        save_dir = Path(".goldenmatch/domains")

    rulebook = DomainRulebook(
        name=name,
        signals=args.get("signals", []),
        identifier_patterns=args.get("identifier_patterns", {}),
        brand_patterns=args.get("brand_patterns", []),
        attribute_patterns=args.get("attribute_patterns", {}),
        stop_words=args.get("stop_words", []),
    )

    path = save_rulebook(rulebook, save_dir / f"{name}.yaml")
    return {
        "status": "created",
        "name": name,
        "path": str(path),
        "scope": scope,
        "signals": rulebook.signals,
        "identifier_patterns": list(rulebook.identifier_patterns.keys()),
    }


def _tool_test_domain(domain_name: str, sample_size: int = 10) -> dict:
    """Test a domain rulebook against loaded data."""
    from goldenmatch.core.domain_registry import discover_rulebooks

    if not _rows:
        return {"error": "No data loaded. Start the MCP server with --file."}

    rulebooks = discover_rulebooks()
    if domain_name not in rulebooks:
        return {"error": f"Domain '{domain_name}' not found. Available: {list(rulebooks.keys())}"}

    rb = rulebooks[domain_name]
    # Get text columns
    sample_cols = [c for c in _rows[0].keys() if not c.startswith("__") and isinstance(_rows[0].get(c), str)]
    if not sample_cols:
        return {"error": "No text columns found in data."}

    text_col = sample_cols[0]
    results = []
    for row in _rows[:sample_size]:
        text = str(row.get(text_col, "") or "")
        extracted = rb.extract(text)
        results.append({
            "original": text[:100],
            "brand": extracted.get("brand"),
            "identifiers": extracted.get("identifiers", {}),
            "name_normalized": extracted.get("name_normalized"),
            "confidence": round(extracted.get("confidence", 0), 2),
        })

    return {
        "domain": domain_name,
        "text_column": text_col,
        "sample_size": len(results),
        "extractions": results,
    }


def _tool_pprl_auto_config(security_level: str = "high", use_llm: bool = False) -> dict:
    """Auto-configure PPRL parameters from loaded data."""
    if not _rows:
        return {"error": "No data loaded. Start the MCP server with --file."}

    import polars as pl

    from goldenmatch.pprl.autoconfig import auto_configure_pprl, auto_configure_pprl_llm

    df = pl.DataFrame(_rows)

    if use_llm:
        result = auto_configure_pprl_llm(df, security_level=security_level)
    else:
        result = auto_configure_pprl(df, security_level=security_level)

    return {
        "recommended_fields": result.recommended_fields,
        "threshold": result.recommended_config.threshold,
        "security_level": result.recommended_config.security_level,
        "ngram_size": result.recommended_config.ngram_size,
        "hash_functions": result.recommended_config.hash_functions,
        "bloom_filter_size": result.recommended_config.bloom_filter_size,
        "explanation": result.explanation,
        "field_profiles": [
            {
                "column": p.column,
                "field_type": p.field_type,
                "avg_length": round(p.avg_length, 1),
                "cardinality": p.cardinality,
                "usefulness_score": round(p.usefulness_score, 2),
            }
            for p in result.field_profiles
        ],
    }


def _tool_pprl_link(args: dict) -> dict:
    """Run PPRL linkage between two files."""
    import polars as pl

    from goldenmatch.pprl.protocol import PPRLConfig, run_pprl

    file_a = _safe_path_or_error(args["file_a"])
    if isinstance(file_a, dict):
        return file_a
    file_b = _safe_path_or_error(args["file_b"])
    if isinstance(file_b, dict):
        return file_b
    if not file_a.exists():
        return {"error": f"File not found: {file_a}"}
    if not file_b.exists():
        return {"error": f"File not found: {file_b}"}

    fields = args["fields"]
    threshold = args.get("threshold", 0.85)
    security_level = args.get("security_level", "high")

    _LEVELS = {"standard": (2, 20, 512), "high": (2, 30, 1024), "paranoid": (3, 40, 2048)}
    ngram, hashes, size = _LEVELS.get(security_level, (2, 30, 1024))

    config = PPRLConfig(
        fields=fields, threshold=threshold, security_level=security_level,
        ngram_size=ngram, hash_functions=hashes, bloom_filter_size=size,
    )

    df_a = pl.read_csv(file_a)
    df_b = pl.read_csv(file_b)

    result = run_pprl(df_a, df_b, config)

    cluster_summary = []
    for cid, members in sorted(result.clusters.items())[:20]:
        cluster_summary.append({
            "cluster_id": cid,
            "members": [{"party": pid, "record_id": rid} for pid, rid in members],
        })

    return {
        "clusters_found": len(result.clusters),
        "match_pairs": result.match_count,
        "total_comparisons": result.total_comparisons,
        "security_level": security_level,
        "threshold": threshold,
        "fields": fields,
        "clusters": cluster_summary,
    }


async def run_server(file_paths: list[str], config_path: str | None = None) -> None:
    """Run the MCP server over stdio."""
    server = create_server(file_paths, config_path)

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def _load_clusters_json(path: str) -> dict[int, dict]:
    """Load a clusters JSON file into the {cluster_id: {"members": [...]}} shape.

    Accepts either a bare cluster mapping or a {"clusters": {...}} wrapper, and
    cluster values that are either a dict with a "members" list or a bare list.
    """
    with open(path, encoding="utf-8") as fp:
        data = json.load(fp)
    raw = data.get("clusters", data) if isinstance(data, dict) else data
    out: dict[int, dict] = {}
    for k, v in raw.items():
        members = v.get("members") if isinstance(v, dict) else v
        out[int(k)] = {"members": [int(m) for m in members]}
    return out


def _tool_evaluate(ground_truth_path: str, col_a: str = "id_a", col_b: str = "id_b") -> dict:
    from goldenmatch.core.evaluate import evaluate_clusters, load_ground_truth_csv
    rs = _resolve_run_state()
    if rs.result is None:
        return {"error": "No run loaded. Run agent_deduplicate (or dedupe_file) in this session first."}
    result_clusters = getattr(rs.result, "clusters", None)
    if result_clusters is None:
        return {"error": "No clusters on this run (a match_sources run has matched/unmatched records, not clusters)."}
    validated = _safe_path_or_error(ground_truth_path)
    if isinstance(validated, dict):
        return validated
    gt = load_ground_truth_csv(str(validated), col_a, col_b)
    return evaluate_clusters(result_clusters, gt).summary()


def _tool_analyze_blocking(
    sample_size: int = 1000, target_block_size: int = 5000, limit: int = 10
) -> dict:
    from dataclasses import asdict

    from goldenmatch.core.block_analyzer import analyze_blocking
    if _engine is None or _config is None:
        return {"error": "No dataset loaded"}
    cols = sorted({f.field for mk in _config.get_matchkeys() for f in mk.fields})
    suggestions = analyze_blocking(
        _engine.data, cols, sample_size=sample_size, target_block_size=target_block_size
    )
    return {
        "matchkey_columns": cols,
        "suggestions": [asdict(s) for s in suggestions[:limit]],
    }


def _tool_compare_clusters(clusters_a_path: str, clusters_b_path: str) -> dict:
    from goldenmatch.core.compare_clusters import compare_clusters
    va = _safe_path_or_error(clusters_a_path)
    if isinstance(va, dict):
        return va
    vb = _safe_path_or_error(clusters_b_path)
    if isinstance(vb, dict):
        return vb
    a = _load_clusters_json(str(va))
    b = _load_clusters_json(str(vb))
    return compare_clusters(a, b).summary()


def _tool_schema_match(file_a: str, file_b: str, min_score: float = 0.5) -> dict:
    from goldenmatch.core.ingest import load_file
    from goldenmatch.core.schema_match import auto_map_columns
    va = _safe_path_or_error(file_a)
    if isinstance(va, dict):
        return va
    vb = _safe_path_or_error(file_b)
    if isinstance(vb, dict):
        return vb
    df_a = load_file(str(va)).collect()
    df_b = load_file(str(vb)).collect()
    return {"mappings": auto_map_columns(df_a, df_b, min_score=min_score)}


def _tool_lineage(
    max_pairs: int = 100, natural_language: bool = False, output_dir: str | None = None
) -> dict:
    from goldenmatch.core.lineage import build_lineage, save_lineage
    if _result is None or _engine is None or _config is None:
        return {"error": "No dataset loaded"}
    if output_dir is not None:
        vdir = _safe_path_or_error(output_dir)
        if isinstance(vdir, dict):
            return vdir
        output_dir = str(vdir)
    lineage = build_lineage(
        _result.scored_pairs,
        _engine.data,
        _config.get_matchkeys(),
        _result.clusters,
        max_pairs=max_pairs,
        natural_language=natural_language,
    )
    from goldenmatch.config.schemas import GoldenRulesConfig
    from goldenmatch.core.lineage import (
        _serialize_golden_records,
        golden_provenance_for_run,
    )
    golden_rules = getattr(_config, "golden_rules", None) or GoldenRulesConfig(default_strategy="most_complete")
    gp = golden_provenance_for_run(_engine.data, _result.clusters, golden_rules)
    if output_dir:
        path = save_lineage(lineage, output_dir, run_name="mcp", golden_provenance=gp)
        return {"saved_to": str(path), "count": len(lineage)}
    out = {"count": len(lineage), "lineage": lineage}
    if gp:
        out["golden_records"] = _serialize_golden_records(gp)
    return out


def _tool_list_runs(output_dir: str = ".") -> dict:
    from goldenmatch.core.rollback import list_runs
    vdir = _safe_path_or_error(output_dir)
    if isinstance(vdir, dict):
        return vdir
    return {"runs": list_runs(str(vdir))}


def _tool_rollback(run_id: str, output_dir: str = ".") -> dict:
    from goldenmatch.core.rollback import rollback_run
    vdir = _safe_path_or_error(output_dir)
    if isinstance(vdir, dict):
        return vdir
    return rollback_run(run_id, str(vdir))


def _tool_config_weaknesses(max_findings: int = 6, phrasing: str = "plain") -> dict:
    """Diagnose weaknesses in the loaded run's auto-config (see core.config_critique)."""
    from goldenmatch.core.config_critique import diagnose_config
    return diagnose_config(
        _engine.data, _config, _result,
        max_findings=max_findings, phrasing=phrasing,
    )


def resolve_http_auth_token(host: str) -> str | None:
    """Return the MCP HTTP bearer token, enforcing the fail-closed bind rule.

    Raises ``RuntimeError`` when binding to a non-loopback host without
    ``GOLDENMATCH_MCP_TOKEN`` set, so an exposed server is never started
    unauthenticated by accident. Returns the token (or ``None`` for an
    intentionally-open loopback bind).

    Escape hatch: set ``GOLDENMATCH_MCP_ALLOW_PUBLIC=1`` to intentionally
    run an open, unauthenticated public server (e.g. a showcase deployment).
    This opts out of the fail-closed default; a set ``GOLDENMATCH_MCP_TOKEN``
    still takes precedence and is enforced when present.
    """
    import os

    token = os.environ.get("GOLDENMATCH_MCP_TOKEN")
    is_loopback = host in ("127.0.0.1", "localhost", "::1")
    allow_public = os.environ.get("GOLDENMATCH_MCP_ALLOW_PUBLIC", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if not token and not is_loopback and not allow_public:
        raise RuntimeError(
            f"Refusing to start an unauthenticated MCP HTTP server on host {host!r}. "
            "Set GOLDENMATCH_MCP_TOKEN, bind to 127.0.0.1 for local use, or set "
            "GOLDENMATCH_MCP_ALLOW_PUBLIC=1 to intentionally run an open public server."
        )
    return token


async def run_server_http(
    host: str = "0.0.0.0",
    port: int = 8200,
    file_paths: list[str] | None = None,
    config_path: str | None = None,
) -> None:
    """Run the MCP server over Streamable HTTP (for hosted deployments).

    Auth: when ``GOLDENMATCH_MCP_TOKEN`` is set, every ``/mcp`` request must
    carry ``Authorization: Bearer <token>``. To prevent shipping an open public
    server, binding to a non-loopback host WITHOUT a token is refused at
    startup (fail closed). The ``/.well-known/`` server card stays public for
    healthchecks.
    """
    import contextlib
    from collections.abc import AsyncIterator

    import uvicorn
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse
    from starlette.routing import Mount, Route

    token = resolve_http_auth_token(host)

    class _BearerAuthMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            if request.url.path.startswith("/.well-known/"):
                return await call_next(request)
            if token:
                header = request.headers.get("Authorization", "")
                if not header.startswith("Bearer ") or header[7:] != token:
                    return JSONResponse({"error": "Unauthorized"}, status_code=401)
            return await call_next(request)

    server = create_server(file_paths or [], config_path)
    session_manager = StreamableHTTPSessionManager(
        app=server,
        stateless=True,
    )

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        async with session_manager.run():
            yield

    async def server_card(request):
        return JSONResponse({
            "name": "GoldenMatch",
            "description": "Entity resolution toolkit — deduplicate records, match across datasets, and create golden records using fuzzy, probabilistic, and LLM-powered scoring. Zero-config mode auto-detects your data. 75 MCP tools for matching, semantic retrieval, explaining, reviewing, evaluating, blocking analysis, config critique, config healing, lineage, data quality, transforms, identity graph, distributed-routing config, privacy-preserving linkage, and inline file upload. Built on Polars. 97.2% F1 on DBLP-ACM.",
            "homepage": "https://github.com/benseverndev-oss/goldenmatch",
            "iconUrl": "https://avatars.githubusercontent.com/u/192581748"
        })

    app = Starlette(
        routes=[
            Route("/.well-known/mcp/server-card.json", server_card),
            Mount("/mcp", app=session_manager.handle_request),
        ],
        lifespan=lifespan,
        middleware=[Middleware(_BearerAuthMiddleware)],
    )

    config = uvicorn.Config(app, host=host, port=port)
    uv_server = uvicorn.Server(config)
    await uv_server.serve()
