# GoldenPipe orchestrator pivot — the streaming data plane (pillar 2)

**Status:** framing / not started. **Owner:** ER platform. **Created:** 2026-07-06.

This doc closes the GoldenCheck cutover chapter and opens the GoldenPipe one. It
is the grounded starting point for pillar 2 of the Rust-cutover thesis
("Smart Pipe, Dumb Kernels"), written against what the code actually is today —
not the aspiration — so the pivot work starts from a real map.

## Where the program stands (context)

The thesis: write the muscle once in pure, dependency-free Rust; use Arrow for
zero-copy memory; let the multi-language surfaces fall out as thin wrappers. Four
pillars: (1) evict Polars/DataFusion for bespoke Rust; (2) Smart-Pipe/Dumb-Kernels
orchestration; (3) zero-copy Arrow FFI; (4) scaffolding-cutover (keep the proven
Python/TS as the reference, prove parity, delete the guts).

**GoldenCheck wave — just completed** (two open PRs at time of writing):
- **SQL surface (P5)** — DuckDB + Postgres `goldencheck_*` functions over
  `goldencheck-core`; the first *aggregate-shaped* SQL surface in the suite. This
  completes GoldenCheck's cross-surface parity row.
- **Zero-copy Arrow FFI** — benford hands the Arrow buffer slice straight to the
  kernel (no `to_vec`); interning reads buffer slices with a no-null fast path; a
  **dictionary-array fast path** interns categorical columns ~2× faster by using
  the Arrow dictionary *as* the interning.
- **Polars-free kernel fallbacks** — FD/composite fallbacks moved off Polars
  `n_unique` to pure-Python set counting. Polars is out of the kernel *compute*
  path (the scanner substrate stays Polars).
- **Deferred, by decision:** the full *native-required* flip (delete the fallback,
  make `goldencheck-native` a hard dep). It crashes plain-install `--deep` scans
  and breaks `uv sync --all-packages` on the shared CI lane, and no suite package
  has gone native-required (goldenmatch keeps native optional; goldenflow keeps a
  fallback). Parked until a deliberate "the suite is wheels-only" decision, done
  once across all `*-native` packages.

The cutover has now touched the *kernels* (goldenmatch, goldenflow, goldencheck,
goldenanalysis) and their *surfaces*. The one pillar barely started is **pillar 2:
the orchestrator**.

## What GoldenPipe is today (grounded)

| Layer | Reality |
|---|---|
| Package (`packages/python/goldenpipe/`, v1.3.0) | Mature Python orchestrator: CLI/TUI/REST/MCP/A2A. |
| Runtime | `pipeline.py` → `engine/resolver.py` → `engine/runner.py`. `Runner.run` is a **sequential Python `for`-loop** over the planned stages (`remaining.pop(0)`; single-threaded, no pooling). |
| Data handoff | A `PipeContext` holding `ctx.df` — a **Polars DataFrame** — plus an `artifacts` dict. Stages are `adapters/{check,flow,match,identity,analysis}.py`, thin wrappers calling the sibling packages' **high-level Python APIs** (`goldencheck.scan_file(path)`, `goldenflow.transform_df(df)`, `goldenmatch.dedupe_df(df)`). |
| Rust (`goldenpipe-core` / `-native` / `-wasm`) | A **planner, not an executor**. `resolve.rs` is a real dependency-DAG resolver (config-order-authoritative, `needs` edges, stable Kahn topo-sort, tagged `PlanError`s); `router.rs`/`decisions.rs`/`config.rs` are pure predicates + auto-config. `-native`/`-wasm` are 5-function marshaling shims over the JSON planner API. `model.rs` states outright: *the Polars `df` never enters the core*; execution/IO is *deliberately* left in the per-language host. |

So the "Smart Pipe" today is a **smart planner + a dumb sequential Python runner**,
composing Polars DataFrames via ordinary Python calls.

## The gap to the pillar-2 endgame

| Thesis claim | Today | Gap |
|---|---|---|
| "manages the execution graph" | Planner in Rust (`resolve.rs`); executor is a Python `for`-loop | Executor isn't in `-core`, isn't Rust, does no scheduling |
| "thread pooling, memory allocation" | None — sequential; Polars owns memory | Entirely unbuilt |
| "streams Arrow data through the isolated `-core` kernels" | Passes whole Polars DataFrames to sibling **Python** APIs | No Arrow handoff; kernels invoked at the Python-package level, not the `*-core` Rust level |
| "processes through `-core` crates, passes the memory address back" | `goldenpipe-core` never sees `df` by design; the other kernels' `-core` crates aren't wired into goldenpipe at all | The Arrow/FFI data plane does not exist |

**Net:** the Arrow-streaming data plane — the load-bearing claim of pillar 2 — is
greenfield. There is no plan and no roadmap entry for it (the cross-surface parity
roadmap is *kernel*-scoped and does not mention goldenpipe).

## Proposed direction (measure-first, staged)

The repo's hardest-won lesson — *"measure, the naive kernel LOST"* — governs here
even more than usual, because a Rust streaming executor is a large build and the
incumbent (in-process Polars DataFrame handoff) is **already fast and largely
zero-copy within a process**. Before building an executor, prove it pays.

- **Stage 0 — Baseline & justify (do this first, cheap).** Instrument a real
  multi-stage pipeline (`scan → transform → dedupe → analyze`) and measure where
  the wall actually goes: per-stage compute vs the DataFrame handoffs between
  stages. **Hypothesis to disprove:** the handoffs are already cheap (Polars
  shares buffers in-process), so an Arrow-streaming executor would optimize a
  non-bottleneck. If the handoffs *aren't* the cost, stop — pillar 2's value is
  elsewhere (e.g. cross-language/out-of-process pipelines, or batched streaming
  for out-of-core data that doesn't fit a single DataFrame).
- **Stage 1 — Arrow at the seams (only where Stage 0 justifies it).** Where a
  stage boundary crosses a language/process line (or spills out-of-core), pass
  Arrow record batches instead of materialized DataFrames — reusing the exact
  zero-copy FFI the kernels already speak (the goldencheck dict-array fast path,
  for one, gets ~2× *for free* the moment a categorical column streams across as an
  Arrow dictionary instead of a re-materialized DataFrame). This is packaging the
  data plane the `*-core` crates already accept, not new kernel work.
- **Stage 2 — Executor (only if streaming + scheduling win).** A Rust executor
  driving the existing `ExecutionPlan` (thread pool over independent DAG branches,
  back-pressured record-batch streaming). This is the biggest lift and the least
  justified today; gate it hard on Stage 0/1 evidence.

Keep the pillar-4 discipline throughout: the Python `Runner` is the proven
reference; any Rust executor must produce byte-identical stage outputs against it
before the Python guts are deleted.

## Open questions / risks

1. **Is there a measured win at all for the single-process case?** In-process
   Polars handoff may already be near-zero-copy. The pivot must not assume the
   FFI-translation cost the thesis cites — goldencheck showed that cost is *already*
   near-zero once Arrow C Data Interface is in play; the real costs were kernel
   compute and interning, not the boundary.
2. **Where is streaming actually load-bearing?** Likely out-of-core (data bigger
   than memory) and cross-process/cross-language pipelines — not the CSV-in-a-
   DataFrame path most `goldenpipe` runs take today. Scope the target workload
   before the executor.
3. **CI gap.** `goldenpipe_native` and `goldenpipe_wasm` are **not** in the
   `ci-required` gate (they're opt-in, path-filtered) — unlike `native_flow` /
   `goldenhnsw` / `wasm_flow`. If the planner (or a future executor) becomes
   load-bearing, promoting these to required is a prerequisite so the parity gates
   actually block the merge queue.

## Reference paths

- Runtime: `packages/python/goldenpipe/goldenpipe/{pipeline.py, engine/runner.py, adapters/*.py}`
- Planner: `packages/rust/extensions/goldenpipe-core/src/{lib.rs, resolve.rs, model.rs}`; bindings `goldenpipe-{native,wasm}/src/lib.rs`
- Shipped planner plans: `docs/superpowers/plans/2026-07-04-goldenpipe-{core-planner,native-binding,wasm-reroute,dag-contract}.md`
- CI: `.github/workflows/ci.yml` lanes `goldenpipe_native`, `goldenpipe_wasm` (neither in `ci-required`).
