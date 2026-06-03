# Repository Structure & Where Context Lives

## Layout
```
packages/
  python/{goldenmatch,goldencheck,goldenflow,goldenpipe,infermap,goldencheck-types}
  rust/extensions/{native,datafusion-udf,score-core,postgres}
  typescript/goldenmatch        # TS port (npm)
  dbt/ , actions/
docs/superpowers/{specs,plans}  # design docs + implementation plans (workstream artifacts)
scripts/                        # build + bench drivers
.github/workflows/              # CI (ci.yml) + many workflow_dispatch benches
context-network/                # THIS network
```

## Where the authoritative context is (do not duplicate into the network)
- **Root `CLAUDE.md`** — monorepo-wide ops: TS/pnpm+turbo, CI structure + path filters,
  Railway services, ghcr, GitHub auth dance, merge SOP, publish workflows, gotchas.
- **`packages/python/CLAUDE.md`** — uv workspace rules + cross-package friction.
- **`packages/python/goldenmatch/CLAUDE.md`** — the big one: goldenmatch architecture
  index, the full performance history (scale-audit numbers, bucket/ray/duckdb backends,
  the Splink-Spark distributed roadmap phases), accuracy strategy, code patterns,
  gotchas. **Start here for any goldenmatch code question.**
- Each other package has its own `CLAUDE.md`.

## Rust extension crates (relevant to the spine)
- `extensions/native` — the `_native` abi3 kernel (bucket scorer, fingerprints). Built
  via `scripts/build_native.py`. Uses `arrow = 55`.
- `extensions/score-core` — shared rapidfuzz scorers; single source of truth for
  `_native` and the FFI crate.
- `extensions/datafusion-udf` — `goldenmatch_datafusion_udf`: the FFI `ScalarUDF`
  string scorers for the DataFusion spine. Pins `datafusion/datafusion-ffi = 53.x`,
  `arrow = 58` (the arrow-major divergence is WHY it's a separate crate from `native`).

---
**Classification:** foundation/semi-stable • **Last updated:** 2026-06-03
