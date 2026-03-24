# MCP Server

## 9 Tools

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

## Architecture

- Single file: `server.py` — all tools in one module
- `create_server() -> Server` factory with `@server.list_tools()` and `@server.call_tool()` decorators
- `_TOOL_HANDLERS` dict maps tool names to handler functions
- Handlers are sync functions returning dicts (auto-serialized to JSON)
- Stdio transport: `run_server()` uses `mcp.server.stdio.stdio_server()`

## Adding a New Tool

1. Add `Tool(name=..., description=..., inputSchema=...)` to the `TOOLS` list
2. Write `_tool_<name>(arguments: dict) -> dict` handler
3. Register in `_TOOL_HANDLERS` dict

## Gotchas

- `install_domain` saves to `goldencheck_domain.yaml` (NOT `goldencheck_types.yaml`) to avoid overwriting user custom types
- Domain descriptions come from the `description` top-level key in domain YAML files
- MCP is an optional dependency (`pip install goldencheck[mcp]`) — the CLI `mcp-serve` command catches ImportError
