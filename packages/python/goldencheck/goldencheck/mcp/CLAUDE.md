# MCP Server

## 9 Core Tools

| Tool | Description |
|------|-------------|
| `scan` | Scan a file for data quality issues (optional LLM boost + domain) |
| `validate` | Check against pinned rules in goldencheck.yml |
| `profile` | Column-level statistics and health score |
| `health_score` | Quick A-F grade |
| `get_column_detail` | Deep-dive into a specific column |
| `list_checks` | Available profiler checks |
| `list_domains` | Available domain packs (bundled + community) |
| `get_domain_info` | Types in a specific domain pack |
| `install_domain` | Download community domain pack to `goldencheck_domain.yaml` |

## 10 Agent Tools (`pip install goldencheck[agent]`)

| Tool | Description |
|------|-------------|
| `analyze_data` | Profile columns, detect domain, recommend profiling strategy |
| `auto_configure` | Generate goldencheck.yml from data analysis + auto-triage |
| `explain_finding` | Natural language explanation for a finding |
| `explain_column` | Column health narrative with findings and profile |
| `review_queue` | List pending review items for a job |
| `approve_reject` | Pin or dismiss a review item |
| `compare_domains` | Scan with each domain pack, report best fit |
| `suggest_fix` | Preview fixes without applying |
| `pipeline_handoff` | Quality attestation for downstream tools |
| `review_stats` | Counts by status (pending/pinned/dismissed) |

## Architecture

- Core tools: `server.py` — 9 tools in one module
- Agent tools: `agent_tools.py` — 10 tools, merged into server at import time (graceful fallback if not installed)
- `create_server() -> Server` factory with `@server.list_tools()` and `@server.call_tool()` decorators
- `_TOOL_HANDLERS` dict maps tool names to handler functions
- Handlers are sync functions returning dicts (auto-serialized to JSON)
- Stdio transport: `run_server()` uses `mcp.server.stdio.stdio_server()`

## Adding a New Tool

**Core tools** (in `server.py`):
1. Add `Tool(name=..., description=..., inputSchema=...)` to the `TOOLS` list
2. Write `_tool_<name>(arguments: dict) -> dict` handler
3. Register in `_TOOL_HANDLERS` dict

**Agent tools** (in `agent_tools.py`):
1. Add `Tool(...)` to the `AGENT_TOOLS` list
2. Write handler function
3. Register in `_AGENT_TOOL_HANDLERS` dict

## Gotchas

- `install_domain` saves to `goldencheck_domain.yaml` (NOT `goldencheck_types.yaml`) to avoid overwriting user custom types
- Domain descriptions come from the `description` top-level key in domain YAML files
- MCP is an optional dependency (`pip install goldencheck[mcp]`) — the CLI `mcp-serve` command catches ImportError
- Agent tools are an optional dependency (`pip install goldencheck[agent]`) — server.py catches ImportError and falls back to 9 core tools only
