# Changelog

## 1.0.1 (2026-03-29)

- Add MCP Registry metadata (server.json, mcp-name verification)
- Add CI test workflow (ruff + pytest)
- Add community files (CODE_OF_CONDUCT.md, SECURITY.md)
- Fix version mismatch in __init__.py
- Clean up tracked internal files

## 1.0.0 (2026-03-29)

First stable release.

### Features
- End-to-end pipeline: GoldenCheck → GoldenFlow → GoldenMatch
- Adaptive logic: skips unnecessary stages, detects PPRL needs
- Pluggable stages via entry points
- 4 MCP tools: list_stages, validate_pipeline, run_pipeline, explain_pipeline
- CLI, REST API, TUI, MCP server, A2A protocol interfaces
- Zero-config default with rich YAML configuration support
