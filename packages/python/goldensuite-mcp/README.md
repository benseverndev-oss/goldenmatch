# goldensuite-mcp

> One MCP server exposing **every** Golden Suite tool — `goldenmatch`, `goldencheck`, `goldenflow`, `goldenpipe`, `infermap` — under a single endpoint.

```bash
pip install goldensuite-mcp
goldensuite-mcp serve --transport http --port 8300
```

Or via container:

```bash
docker run -p 8300:8300 ghcr.io/benzsevern/goldensuite-mcp:latest
```

## What it does

`goldensuite-mcp` imports each sub-package's MCP tool list and dispatcher, composes them into a single `mcp.server.Server` instance, and serves them over stdio or Streamable HTTP.

You point your MCP client at one endpoint and get the full Golden Suite — entity resolution, data quality scanning, transforms, pipeline orchestration, and schema mapping.

## Tool collisions

Tool names register on a **first-wins** basis. The registration order is:

1. **goldenmatch** — entity resolution (headline package; its tools win collisions)
2. **goldencheck** — data quality scanning
3. **goldenflow** — transforms & standardizers
4. **goldenpipe** — pipeline orchestrator
5. **infermap** — schema mapping

If two packages register a tool with the same name, the later one is shadowed. **Shadowed tools are logged at WARNING level when the server starts**, so you can see exactly what happened:

```
WARNING goldensuite_mcp.server: tool collision: 'profile' from goldenflow shadowed by earlier goldencheck (first-wins)
```

If you need a shadowed tool, use that package's standalone MCP server instead (e.g. `goldenflow mcp-serve`).

## Claude Desktop / Claude Code config

```json
{
  "mcpServers": {
    "goldensuite": {
      "command": "goldensuite-mcp",
      "args": ["serve"]
    }
  }
}
```

Or the hosted variant (when one is published):

```json
{
  "mcpServers": {
    "goldensuite": {
      "url": "https://goldensuite-mcp.example/mcp/"
    }
  }
}
```

## Why an aggregator?

The Golden Suite ships five Python packages, each with its own MCP server (`goldenmatch mcp-serve`, `goldencheck mcp-serve`, …). For a deployer running all five behind one Claude Desktop config, that's five processes and five mounts.

`goldensuite-mcp` is the convenience option:
- One process, one mount, all the tools
- Identical Tool definitions (no proxying or naming changes)
- Sub-package MCP servers continue to work standalone for narrower deployments

## Architecture

```
                 ┌──────────────────────────────────────────┐
                 │   goldensuite-mcp Server                  │
                 │   (one mcp.server.Server instance)        │
                 └─────────────┬────────────────────────────┘
                               │ aggregates
        ┌──────────────────────┼──────────────────────┐
        ▼                      ▼                      ▼
   goldenmatch.mcp       goldencheck.mcp        goldenflow.mcp     ...
   TOOLS + dispatch      TOOLS + dispatch       TOOLS + handle_tool
```

Each sub-package exposes its `TOOLS` list and a dispatcher at module scope (`goldenmatch.mcp.server.dispatch`, `goldencheck.mcp.server._TOOL_HANDLERS`, etc.). The aggregator imports those, normalizes Tool format (some are `Tool` objects, some are dicts), and binds tool names to the right dispatcher.

No subprocess overhead, no IPC. All tool calls execute in-process.

## Standalone vs aggregator

| Use case | Recommend |
|---|---|
| Need only one Golden Suite package's tools | `<package> mcp-serve` (standalone) |
| Want everything in one MCP endpoint | `goldensuite-mcp serve` |
| Care about tool collisions | Read the WARNING logs at startup, or use standalone |
| Need different versions of sub-packages on different endpoints | Use standalone |

## License

MIT — see [LICENSE](../../../LICENSE) at repo root.
