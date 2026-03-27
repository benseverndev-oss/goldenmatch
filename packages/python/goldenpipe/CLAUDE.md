# GoldenPipe

Golden Suite orchestrator -- chains GoldenCheck, GoldenFlow, GoldenMatch.

## Related Projects
- **GoldenCheck:** `D:\show_case\goldencheck` -- Data validation. Has its own CLAUDE.md.
- **GoldenFlow:** `D:\show_case\goldenflow` -- Data transformation. Has its own CLAUDE.md.
- **GoldenMatch:** `D:\show_case\goldenmatch` -- Entity resolution. Has its own CLAUDE.md.
- **GitHub:** `benzsevern/goldenpipe`, `benzsevern/goldencheck`, `benzsevern/goldenflow`, `benzsevern/goldenmatch`

## Branch & Merge SOP (all Golden Suite repos)
- Feature work goes on `feature/<name>` branches, never directly to main
- Merge via **squash merge PR** (watchers see PR activity, history stays clean)
- PR title format: `feat: <description>` or `fix: <description>`
- Merge when: tests pass, docs updated. Days not weeks.
- After merge: delete remote branch

## Environment
- Windows 11, bash shell (Git Bash)
- Python 3.12 at `C:\Users\bsevern\AppData\Local\Programs\Python\Python312\python.exe`
- Two GitHub accounts: `benzsevern` (personal) and `benzsevern-mjh` (work)
- MUST `gh auth switch --user benzsevern` before push, switch back to `benzsevern-mjh` after

## Architecture
- `goldenpipe/pipeline.py` -- Pipeline class, run() function. ONLY file that imports from tools.
- `goldenpipe/decisions.py` -- Adaptive logic (decide_flow, decide_match). NO tool imports. Testable independently.
- `goldenpipe/cli/main.py` -- Typer CLI
- Tools imported with try/except ImportError guards (HAS_CHECK, HAS_FLOW, HAS_MATCH)
- Data flows as Polars DataFrames in memory between stages

## Pipeline Flow
```
load_file -> GoldenCheck.scan_file(path) -> decide_flow(findings)
  -> if fixable: GoldenFlow.transform_df(df) -> updated df
  -> decide_match(findings, row_count, strategy_override)
  -> GoldenMatch.dedupe_df(df) or AgentSession.deduplicate(path)
  -> PipeResult
```

## Testing
- `pytest --tb=short` from project root
- test_decisions.py: no tool deps, tests pure decision logic
- test_pipeline.py: requires goldencheck, goldenflow, goldenmatch installed

## A2A Port Convention
- GoldenCheck: 8100, GoldenFlow: 8150, GoldenMatch: 8200, GoldenPipe: 8250

## Remote MCP Server

Hosted on Railway, registered on Smithery:
- **Endpoint:** `https://goldenpipe-mcp-production.up.railway.app/mcp/`
- **Smithery:** `https://smithery.ai/servers/benzsevern/goldenpipe`
- **Server card:** `https://goldenpipe-mcp-production.up.railway.app/.well-known/mcp/server-card.json`
- **Transport:** Streamable HTTP (via `StreamableHTTPSessionManager`)
- **Dockerfile:** `Dockerfile.mcp` (Python 3.12-slim, installs `.[mcp]`)
- **Railway project:** `golden-suite-mcp` (service: `goldenpipe-mcp`, port 8250)
- **Local HTTP:** `goldenpipe mcp-serve --transport http --port 8250`
