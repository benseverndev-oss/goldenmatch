# Integrating the Golden Suite (agent guide)

You are wiring the Golden Suite into a project. This is the canonical setup — follow it
instead of re-deriving one. If you only read one thing: **`goldenpipe` is the front door.
Install `golden-suite`, drive `goldenpipe`, reach for individual tools only when you need
a single capability.**

`pip install golden-suite` gives you the whole suite **plus native acceleration, defaulted
to the perf-optimized configuration** — no env vars to set. It should never silently run
the slow pure-Python path; `golden-suite doctor` verifies that and `golden-suite optimize`
repairs it.

## The suite in one screen

| Package | PyPI | What it does | Import |
| --- | --- | --- | --- |
| **GoldenPipe** | `goldenpipe` | Orchestrator. Chains the tools as pluggable stages. **Start here.** | `import goldenpipe as gp` |
| **GoldenMatch** | `goldenmatch` | Entity resolution: dedupe, match across sources, golden records | `import goldenmatch as gm` |
| **GoldenCheck** | `goldencheck` | Data validation — discovers rules from the data, no rule-writing | `import goldencheck` |
| **GoldenFlow** | `goldenflow` | Transform / standardize / normalize messy data | `import goldenflow` |
| **GoldenSchema** | `infermap` | Inference-driven schema mapping (import name is `infermap`) | `import infermap` |
| **GoldenAnalysis** | `goldenanalysis` | Read-only cross-cutting metrics + reporting | `import goldenanalysis` |
| `goldencheck-types` | `goldencheck-types` | Shared field-type contracts (transitive; you won't install directly) | — |
| `goldensuite-mcp` | `goldensuite-mcp` | One MCP server exposing every tool (the agent front door) | — |

Dependency shape (a clean DAG — **GoldenMatch is a leaf, not the root**):

```
goldencheck-types  ──►  everything (shared contracts)
infermap (GoldenSchema) ─┐
goldenmatch ─────────────┤
goldencheck ─────────────┼──►  goldenpipe  ──►  golden-suite (this meta-package)
goldenflow  ─────────────┤            └──► goldensuite-mcp (all tools, one MCP)
goldenanalysis ──────────┘
```

## Install — pick ONE line

Native acceleration is **included by default** (it's a hard dependency, not an extra).

| You want... | Install |
| --- | --- |
| The whole suite + native, perf-optimized | `pip install golden-suite` |
| Suite + one MCP server for agents | `pip install "golden-suite[mcp]"` |
| Everything (suite + mcp + serving) | `pip install "golden-suite[all]"` |
| Just entity resolution | `pip install goldenmatch` |
| Just validation | `pip install goldencheck` |
| Orchestrator + the three core tools only | `pip install "goldenpipe[golden-suite]"` |

Supported native platforms: Linux x86_64/aarch64, macOS x86_64/arm64, Windows amd64. On a
platform without a published wheel the install **fails loudly** (by design — the suite does
not silently degrade to pure-Python). Those users install the individual pure-Python
packages directly instead of `golden-suite`.

## Verify + repair the setup (do this after install)

```bash
golden-suite doctor      # lists every component + whether native is ACTIVE; exits non-zero if silently slow
golden-suite optimize    # installs any missing native kernels for this platform, then re-verifies
```

`doctor` is read-only and CI-safe (non-zero exit when a package is silently on the
pure-Python path). Programmatic equivalents:

```python
from golden_suite import installed, native_status
print(installed())        # {"goldenpipe": "1.2.1", "goldenmatch": "1.30.0", ...}
print(native_status())    # per-package: native_active / silently_slow / env_mode
```

## Three ways to integrate (choose by consumer)

1. **Python API** — you're inside a Python codebase. Import `goldenpipe` (or a single tool).
2. **MCP** — the consumer is an agent/LLM. Run **one** server: `goldensuite-mcp` (or `golden-suite[mcp]`). Do **not** wire six per-package MCP servers by hand.
3. **CLI** — one-off / shell / CI. Every package ships a Typer CLI: `goldenpipe run`, `goldenmatch dedupe`, `goldencheck scan`, etc.

## Canonical quick-starts

### Full pipeline (validate → transform → match), one call

```python
import goldenpipe as gp

result = gp.run("customers.csv")     # zero-config
print(result.status)                 # "success"
print(result.check)                  # quality findings
print(result.transform)              # what got standardized
print(result.match)                  # deduplicated clusters
print(result.reasoning)              # why each decision was made
```

### Just deduplicate

```python
import goldenmatch as gm
result = gm.dedupe("customers.csv")              # zero-config
# explicit:
result = gm.dedupe("customers.csv", exact=["email"], fuzzy={"name": 0.85}, blocking=["zip"])
result.golden.write_csv("deduped.csv")
```

### Match two sources

```python
result = gm.match("crm.csv", "billing.csv", fuzzy={"name": 0.85, "address": 0.80})
```

### Validate (rules discovered from the data)

```python
import goldencheck
report = goldencheck.scan("customers.csv")
```

### Map an unknown schema to a canonical one

```python
import infermap                       # GoldenSchema
mapping = infermap.infer("raw_export.csv")
```

### One MCP server for all of it

```bash
pip install "golden-suite[mcp]"
goldensuite-mcp                        # every suite tool, one server
```

## Anti-patterns that cause the back-and-forth (don't do these)

- **Installing `goldenmatch` and expecting the pipeline / check / transform.** GoldenMatch is
  entity resolution only. For the end-to-end flow use `goldenpipe`.
- **Hand-wiring each tool into a bespoke pipeline.** `goldenpipe` already registers every tool
  as a stage (`goldencheck.scan`, `goldenflow.transform`, `goldenmatch.dedupe`,
  `goldenmatch.identity_resolve`, `goldenanalysis.report`) via entry-points. Use it.
- **Running six MCP servers.** One `goldensuite-mcp` exposes them all.
- **Importing `goldenschema`.** The import name is `infermap` (PyPI/product name is GoldenSchema).
- **Assuming native is off and setting `<PKG>_NATIVE=1` "to turn it on".** It's already on by
  default (`auto` runs the parity-signed-off hot paths native automatically). `=1` is a
  *require-and-force* mode that also runs components NOT yet parity-signed-off (notably
  goldenflow) and **can change outputs** — only use it via `golden-suite optimize --strict`
  after validating parity for your workload.
- **Pinning tools to each other's versions.** They release independently. Let `golden-suite`
  carry the compatible lower bounds; don't hard-pin cross-package versions in the consumer.

## Notes

- Python 3.11–3.13. Everything is Polars-backed.
- Repo: `benseverndev-oss/goldenmatch` (monorepo — all suite packages live here under
  `packages/python/<pkg>`).
- Per-tool detail: each package has its own `AGENTS.md` and `llms.txt`.
