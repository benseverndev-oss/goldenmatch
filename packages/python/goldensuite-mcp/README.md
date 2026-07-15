# goldensuite-mcp

> One MCP server exposing **every** Golden Suite tool — `goldenmatch`, `goldencheck`, `goldenflow`, `goldenpipe`, `infermap`, `goldenanalysis` — under a single endpoint.

```bash
pip install goldensuite-mcp
goldensuite-mcp serve --transport http --port 8300
```

Or via container:

```bash
docker run -p 8300:8300 ghcr.io/benseverndev-oss/goldensuite-mcp:latest
```

## What it does

`goldensuite-mcp` imports each sub-package's MCP tool list and dispatcher, composes them into a single `mcp.server.Server` instance, and serves them over stdio or Streamable HTTP.

You point your MCP client at one endpoint and get the full Golden Suite — entity resolution, data quality scanning, transforms, pipeline orchestration, schema mapping, and trend analysis & regression detection.

## Tool collisions

Tool names register on a **first-wins** basis. The registration order is:

1. **goldenmatch** — entity resolution (headline package; its tools win collisions)
2. **goldencheck** — data quality scanning
3. **goldenflow** — transforms & standardizers
4. **goldenpipe** — pipeline orchestrator
5. **infermap** — schema mapping
6. **goldenanalysis** — trend analysis & regression detection (registered last)

If two packages register a tool with the same name, the later one is shadowed. **Shadowed tools are logged at WARNING level when the server starts**, so you can see exactly what happened:

```
WARNING goldensuite_mcp.server: tool collision: 'profile' from goldenflow shadowed by earlier goldencheck (first-wins)
```

If you need a shadowed tool, use that package's standalone MCP server instead (e.g. `goldenflow mcp-serve`).

## Curated tool listing (`GOLDENSUITE_MCP_TOOLS`)

The full suite is ~105 tools. That many in one flat namespace makes an LLM's
tool-selection noticeably worse, so **`list_tools` returns a curated headline
set (~25 tools) by default** — the primary verbs of each package. Every other
tool stays **fully callable by exact name**; the filter only trims what the
client sees when it *enumerates* tools, never what it can *invoke*.

Control it with the `GOLDENSUITE_MCP_TOOLS` env var:

| Value | `list_tools` returns |
|---|---|
| _unset_ / `curated` | the ~25 headline tools (default) |
| `full` | every aggregated tool (~105) |
| `scan,transform,analyze_data` | exactly those names (whitespace tolerated) |

```bash
# See the whole surface
GOLDENSUITE_MCP_TOOLS=full goldensuite-mcp serve

# Only the tools a given workflow needs
GOLDENSUITE_MCP_TOOLS=upload_dataset,agent_deduplicate,scan goldensuite-mcp serve
```

The curated set lives in `CURATED_TOOLS` in `goldensuite_mcp/server.py`.

### Discovering hidden tools (`suite_find_tools`)

Because the curated listing hides ~80 tools, the default surface includes one
discovery tool, **`suite_find_tools`**, so a client can find and reach the rest
without switching to `full`:

```jsonc
// find everything data-quality related
suite_find_tools({ "query": "quality" })
// list one package's whole surface
suite_find_tools({ "package": "goldenmatch" })
// -> [{ name, package, description, inputSchema }, ...]
```

It returns each matching tool's name, package, description, and input schema.
Call any returned tool by its exact `name` — hidden tools dispatch normally, they
just aren't listed. (`suite_find_tools` does not list itself.) This keeps the
default surface small while leaving the full ~105-tool catalog one search away,
instead of collapsing everything into a few overloaded `action`-style god-tools.

## Composite workflows (one-call happy paths)

The aggregator also registers four **composite** tools that orchestrate the
granular sub-package tools into a single call, so an agent doesn't have to
chain upload -> configure -> match/dedupe by hand. Each is curated (listed by
default) and dispatches against the same aggregated tool table — the granular
tools it calls stay individually listed and callable.

| Composite | Chain | Writes |
| --- | --- | --- |
| **`dedupe_file`** | `upload_dataset` -> `auto_configure` -> `agent_deduplicate` | golden CSV |
| **`match_sources`** | upload A + upload B -> `agent_match_sources` | matches CSV |
| **`assess_file`** | `upload_dataset` -> `analyze_data` -> `scan` | nothing (read-only) |
| **`clean_and_dedupe`** | `upload_dataset` -> one in-process `goldenpipe.run()` (check -> flow -> dedupe) | golden CSV |

Each accepts a file either inline (`file_content` + `filename`) or as an
already-uploaded server path (`file_path`); `match_sources` takes the pair
(`file_a*` / `file_b*`).

```jsonc
// dedupe one CSV end-to-end
dedupe_file({ "file_path": "/data/contacts.csv" })

// link two sources
match_sources({ "file_a": "/data/crm.csv", "file_b": "/data/signups.csv" })

// read-only quality + profile check
assess_file({ "file_path": "/data/contacts.csv" })

// normalize then dedupe
clean_and_dedupe({ "file_path": "/data/contacts.csv" })
```

**Merged return shape** — every composite returns one uniform envelope:

```jsonc
{
  "workflow": "dedupe_file",
  "ok": true,                 // false if a non-degraded step failed
  "summary": "5 records -> 3 golden; 1 merged, 0 to review. Written to /data/contacts.golden.csv.",
  "steps": [                  // one entry per orchestrated step, in order
    { "step": "upload", "ok": true, "path": "/data/contacts.csv" },
    { "step": "auto_configure", "ok": true, "config": { } },
    { "step": "deduplicate", "ok": true, "auto_merge": 1, "review": 0, "golden_path": "/data/contacts.golden.csv" }
  ],
  "config": { },             // when a configure step ran (transparency)
  "outputs": { "golden_path": "/data/contacts.golden.csv", "golden_records": 3, "total_records": 5 }
}
```

A composite **short-circuits** on the first hard step failure (`ok: false`,
`summary` names the failing step). `assess_file` treats its `scan` step as
**degraded-optional**: if goldencheck isn't in the build, `scan` reports
unavailable but the composite still returns `ok: true` with the profile intact.
Writes are guarded by `GOLDENMATCH_ALLOWED_ROOT` like the underlying tools.

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

The Golden Suite ships six Python packages, each with its own MCP server (`goldenmatch mcp-serve`, `goldencheck mcp-serve`, …). For a deployer running all six behind one Claude Desktop config, that's six processes and six mounts.

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
