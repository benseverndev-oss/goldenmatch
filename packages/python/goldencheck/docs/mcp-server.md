---
title: MCP Server
layout: default
nav_order: 16
---

GoldenCheck includes an MCP (Model Context Protocol) server for Claude Desktop and other MCP-compatible clients.

## Remote Server (no install required)

GoldenCheck is available as a hosted remote MCP server on Smithery. Connect from Claude Desktop, Claude Code, or any MCP client without installing anything locally.

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "goldencheck": {
      "url": "https://goldencheck-mcp-production.up.railway.app/mcp/"
    }
  }
}
```

Or browse on Smithery: [https://smithery.ai/servers/benzsevern/goldencheck](https://smithery.ai/servers/benzsevern/goldencheck)

## Local Install

```bash
pip install goldencheck[mcp]
```

### Local Setup (Claude Desktop)

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "goldencheck": {
      "command": "goldencheck",
      "args": ["mcp-serve"]
    }
  }
}
```

Restart Claude Desktop. You'll see GoldenCheck tools available in the tools menu.

## Available Tools

### `scan`

Scan a data file for quality issues. Returns findings with severity, confidence, affected rows, and sample values.

**Parameters:**
| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `file_path` | string | Yes | — | Path to CSV, Parquet, or Excel file |
| `llm_boost` | boolean | No | false | Enable LLM enhancement |
| `llm_provider` | string | No | "anthropic" | "anthropic" or "openai" |
| `sample_size` | integer | No | 100000 | Max rows to sample |

**Returns:** Health grade, score, finding count, and full findings list.

### `validate`

Validate a file against pinned rules in `goldencheck.yml`.

**Parameters:**
| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `file_path` | string | Yes | — | Path to data file |
| `config_path` | string | No | "goldencheck.yml" | Path to config |

**Returns:** Pass/fail status, finding count, and validation findings.

### `profile`

Get column-level statistics: type, null%, unique%, min/max, top values, detected formats.

**Parameters:**
| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `file_path` | string | Yes | — | Path to data file |
| `sample_size` | integer | No | 100000 | Max rows to sample |

**Returns:** Health grade, column profiles with statistics.

### `health_score`

Quick A-F grade for a data file.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `file_path` | string | Yes | Path to data file |

**Returns:** Grade (A-F), numeric score (0-100), error/warning counts.

### `get_column_detail`

Deep-dive into a specific column with full statistics and all findings.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `file_path` | string | Yes | Path to data file |
| `column` | string | Yes | Column name to inspect |

**Returns:** Full column profile including patterns, enum values, and all findings.

### `list_checks`

List all available profiler checks and what they detect. No parameters.

## Example Usage in Claude

> "Scan my sales data at /data/sales_2024.csv and tell me what issues you find"

Claude will call the `scan` tool and present the findings in a readable format.

> "What's the health score of /data/customers.parquet?"

Claude calls `health_score` for a quick summary.

> "Validate /data/orders.csv against my rules"

Claude calls `validate` to check against `goldencheck.yml`.

## CLI

You can also start the server manually:

```bash
goldencheck mcp-serve
```

This starts the stdio-based MCP server. It's primarily designed to be launched by MCP clients like Claude Desktop.

## Protocol

GoldenCheck supports two transport protocols:

- **stdio** (local): The server reads JSON-RPC messages from stdin and writes responses to stdout. Used when launched by MCP clients like Claude Desktop via the `command` config.
- **Streamable HTTP** (remote): The hosted server at `https://goldencheck-mcp-production.up.railway.app/mcp/` uses Streamable HTTP transport. Used when connecting via the `url` config.

Both follow the [MCP specification](https://modelcontextprotocol.io/).

You can also run the HTTP transport locally:

```bash
goldencheck mcp-serve --transport http --port 8100
```
