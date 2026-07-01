# golden-suite

One-line, perf-optimized install and single front door for the whole **Golden Suite**.

```bash
pip install golden-suite      # whole suite + native acceleration, defaulted to the fast config
golden-suite doctor           # verify native is actually active
```

This is a thin meta-package. It pulls in every suite tool **plus the native (Rust)
acceleration kernels, on by default**, and gives you (and your agents) one canonical entry
point. It ships no data-processing logic of its own — just a `doctor`/`optimize` CLI and
introspection helpers.

## What you get

| Tool | Does | Import |
| --- | --- | --- |
| **GoldenPipe** | Orchestrator — chains the tools as pluggable stages. **Start here.** | `import goldenpipe as gp` |
| **GoldenMatch** | Entity resolution: dedupe, match, golden records | `import goldenmatch as gm` |
| **GoldenCheck** | Data validation (rules discovered from your data) | `import goldencheck` |
| **GoldenFlow** | Transform / standardize / normalize | `import goldenflow` |
| **GoldenSchema** | Inference-driven schema mapping (import name: `infermap`) | `import infermap` |
| **GoldenAnalysis** | Read-only metrics + reporting | `import goldenanalysis` |

`goldenpipe` is the front door: it adapts every other tool as a stage, so most integrations
only ever touch `goldenpipe`. `goldenmatch` is a leaf (entity resolution only), not the root.

## Install options

Native acceleration is included by default (a hard dependency, not an extra).

| You want | Install |
| --- | --- |
| The whole suite + native | `pip install golden-suite` |
| Suite + one MCP server | `pip install "golden-suite[mcp]"` |
| Everything (suite + mcp + serving) | `pip install "golden-suite[all]"` |

Native wheels cover Linux x86_64/aarch64, macOS x86_64/arm64, Windows amd64. On an
unsupported platform the install fails loudly rather than silently degrading — install the
individual pure-Python packages directly there.

## Quick start

```python
import goldenpipe as gp

result = gp.run("customers.csv")   # validate -> transform -> match, one call
print(result.status, result.match, result.reasoning)
```

Verify + repair the perf setup:

```bash
golden-suite doctor      # every component + whether native is ACTIVE (non-zero exit if silently slow)
golden-suite optimize    # install any missing native kernels, then re-verify
```

```python
from golden_suite import installed, native_status
print(installed())       # {"goldenpipe": "1.2.1", "goldenmatch": "1.30.0", ...}
print(native_status())   # per-package native_active / silently_slow / env_mode
```

## For agents

See [`AGENTS.md`](./AGENTS.md) and [`llms.txt`](./llms.txt) — the canonical integration guide,
including the anti-patterns that cause most of the "wrong setup" back-and-forth.

## License

MIT. Part of the [Golden Suite monorepo](https://github.com/benseverndev-oss/goldenmatch).
