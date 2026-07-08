# GoldenPipe Compiler — Sub-project 1: IR Walking Skeleton — Design

**Date:** 2026-07-08
**Status:** Approved (brainstorming), pending implementation plan
**Program:** GoldenPipe as a fuse-and-emit compiler (full IR + optimizer + codegen). This
is **sub-project 1 of a multi-spec program**; later sub-projects add optimization
passes (fusion, DCE, pushdown, CSE) and emit backends (SQL, DataFusion). This spec
covers ONLY the walking skeleton.

## Goal

Establish GoldenPipe's compiler foundation: a typed intermediate representation (IR),
a `lower` from today's `ExecutionPlan` into it, and a reference backend that executes
the IR — with an **equivalence gate** proving `compile→execute` reproduces today's
stage-by-stage output byte-for-byte. **No optimization passes and no fused execution
yet** (identity compile via a delegating backend). This de-risks the whole program:
every later pass and backend is an increment on a proven IR + equivalence net.

## Non-goals (explicit scope boundaries)

- **No optimization passes** — identity compile (lower, then execute as-is; no
  fuse/DCE/pushdown/CSE).
- **No fused native execution** — the backend *delegates* to the existing
  goldencheck/goldenflow/goldenmatch engines. Match nodes stay delegating (not
  re-implemented).
- **No emit-to-external** (SQL/DataFusion/Substrait).
- **No TS backend** — the IR + `lower` are cross-surface in the kernel, but the
  executing reference backend is Python-only in #1.
- **Additive + opt-in** — a new entry point / `compile=True`; the **classic runner
  stays the default**. Zero behavior change for existing callers.

## Architecture

A compiler splits along GoldenPipe's existing kernel/host seam:
- **IR + `lower`** live in `goldenpipe-core` (pyo3-free Rust kernel) — pure,
  deterministic, cross-surface, parity-gated by golden vectors. Same pattern as the
  planner (`plan_pipeline`, `resolve`, `apply_decision`).
- **`resolve` + the delegating backend + the equivalence gate** are per-language
  **hosts** (Python-first). `resolve` is data-dependent, so it cannot be in the kernel.

### The three compiler functions

1. **`resolve(stage, artifacts) → concrete_config`** — *host*, data-dependent. Reuses
   existing auto-config machinery (goldencheck profiling, goldenflow auto-detect →
   `TransformSpec`s, goldenmatch `auto_configure`) to produce the explicit config a
   stage would run, from the artifacts available at that point.
2. **`lower(stage_name, concrete_config) → [IrNode]`** — ***kernel***, pure,
   cross-surface, golden-vector'd. Appends fine nodes to the `CompiledPipeline`.
3. **`execute(CompiledPipeline, ctx)`** — *host* delegating backend. Executes each
   stage's node-subgraph by invoking the existing engine.

### Staged resolution (chosen resolution model)

GoldenPipe's stages are data-dependent on each other (Flow auto-detects from the
post-Check df; Match auto-configures from the post-Flow df). So the compiler resolves +
lowers + executes **progressively, in dependency order**:

```
compiled = CompiledPipeline{nodes: [], edges: []}
for stage in execution_plan (dependency order):
    concrete = resolve(stage, ctx.artifacts)        # host: reuse existing auto-config
    nodes    = lower(stage.name, concrete)          # kernel: fine IR nodes
    compiled.append(nodes)                          # accumulate the durable IR
    execute(nodes, ctx)                             # host: delegating execution
    # ctx.artifacts updated by execution, feeding the next stage's resolve
```

This reproduces today's staged behavior exactly → **equivalence by construction**.
Later fusion (sub-project 2) operates on the accumulated IR, fusing only the
statically-known columnar sub-graphs (e.g. adjacent `Map` nodes, `Scan`s over the
input); data-dependent boundaries (Flow→Match) remain barriers.

## The IR

Serde structs in `goldenpipe-core`, JSON-serializable like `ExecutionPlan`.

```
CompiledPipeline { nodes: [IrNode], edges: [(from_id, to_id, artifact)] }   // a DAG

IrNode (tagged union), every variant carries { id, origin_stage, config }:
  Source    { produces: ["df"] }                 // load
  Scan      { column, ops: [ProfileOp] }         // Check  (per-column, fusable)
  Map       { column, op: TransformOp }          // Flow   (per-column, fusable)
  Partition { keys: [BlockKey] }                 // Match blocking   (barrier)
  PairScore { scorer: ScorerSpec }               // Match scoring    (barrier)
  Connected { method: ClusterSpec }              // Match clustering (barrier)
```

- **Edges** are the artifact dataflow (`produces`/`consumes`) the planner already
  tracks (`df`, `findings`, `profile`, `manifest`, `clusters`, `golden`).
- **Op payloads** (`ProfileOp`/`TransformOp`/`ScorerSpec`/`BlockKey`/`ClusterSpec`) are
  captured **verbatim** from the resolved stage config (opaque JSON values in #1 — the
  IR records them faithfully without interpreting them; interpretation is a later pass's
  job).
- **Granularity:** per-column for `Scan`/`Map` (the fusion frontier); Match nodes are
  first-class but treated as barriers.
- **`resolved: bool`** on each node marks whether it came from an auto-configured stage
  (see fidelity note).

## Lowering (per stage)

- `load` → one `Source` node.
- `goldencheck.scan` → one `Scan` node per profiled column, `ops` = the resolved
  check/profile ops for that column.
- `goldenflow.transform` → one `Map` node per `(column, op)` in the resolved
  `transforms: [{column, ops}]` (so a column with N ops lowers to N `Map` nodes in
  order).
- `goldenmatch.dedupe` → `Partition` (blocking keys) + `PairScore` (scorer) +
  `Connected` (cluster method), from the resolved match config.

`lower` is a pure total function of `(stage_name, concrete_config)`; unknown stage
names lower to a single opaque `Barrier`-style node carrying the raw config (forward
compatibility) rather than erroring.

## The delegating reference backend + auto-resolve fidelity

`execute` runs each stage's node-subgraph by **delegating to the existing engine**:
- For an **explicitly-configured** stage, delegate with the explicit config the nodes
  carry.
- For an **auto-configured** stage (`resolved: true`), delegate via the **auto path
  itself** (call the stage as it runs today) — NOT a reconstructed explicit config.
  This guarantees execution equals today's output trivially.

So in #1, the IR *records* the resolved concrete ops (for future passes to consume) but
execution does not yet depend on that record being a perfect explicit substitute.
**Fidelity is still gated:** the equivalence tests additionally assert that the recorded
resolved config matches what actually ran (so the IR is a faithful record, not just an
executable one). Making *fused* execution from the recorded config match auto is
sub-project 2's burden.

## Kernel / host split (for the plan)

- **Kernel** (`goldenpipe-core`, Rust; Python pure mirror): the `IrNode` /
  `CompiledPipeline` structs, `lower`, and a `lower_json` wrapper + golden vectors.
- **Host** (Python): a `Compiler` orchestrator (`resolve` reuse + the staged loop),
  the delegating `execute` backend, and the equivalence gate. New opt-in entry point
  (e.g. `Pipeline.run(compile=True)` or `CompiledRunner`) alongside the classic runner.

## Error handling

- `resolve` failure on a stage → the compiler path raises a typed `CompileError`
  naming the stage; it does NOT silently fall back (opt-in path; failures must be
  visible). The classic runner is unaffected.
- `lower` is total (unknown stage → opaque node, never raises).
- `execute` delegates, so engine errors surface exactly as they do today.
- Empty pipeline / single-stage pipeline → valid `CompiledPipeline` (possibly just
  `Source`), executes to the same artifacts.

## Testing

### Kernel golden vectors for `lower` (cross-surface parity)
Canonical `(stage_name, concrete_config) → IR JSON` cases in
`goldenpipe-core/tests/vectors/lower.json`, replayed by the Rust test and the Python
pure mirror (TS later). Covers: load→Source; a flow config with a multi-op column →
ordered `Map` nodes; a check config → `Scan` nodes; a match config →
`Partition`+`PairScore`+`Connected`; an unknown stage → opaque node.

### Equivalence gate (host, box-runnable — the heart of #1)
Fixture pipelines over tiny fixture datasets, each exercising different node types:
1. `load→flow` with **explicit** transforms (static lowering, no resolve).
2. `load→check→flow` with **auto-detect** (staged resolve + `Scan`/`Map`).
3. Full `load→check→flow→match` producing `clusters`+`golden`
   (`Partition`/`PairScore`/`Connected`).

For each: run the **classic runner** → capture all artifacts; run the **compiler**
(`resolve→lower→execute`) → capture artifacts; assert **byte-identical** across `df`,
`findings`, `profile`, `manifest`, `clusters`, `golden`. Plus a **fidelity assertion**:
the recorded IR's resolved config matches what ran. Tiny datasets keep goldenmatch/polars
fast on the box (`GOLDENMATCH_NATIVE=0`, `POLARS_SKIP_CPU_CHECK=1`).

### Structural IR tests
`compile` (without execute) on the fixtures produces the expected node/edge shape
(node types, per-column `Map` counts, the Match triple, edges = artifact dataflow).

## Rollout

Additive + opt-in; classic runner stays default. No new kernel symbols depended on by
existing paths. The compiler entry point is inert until explicitly invoked.

## What this unlocks (the program, for context — NOT built here)

- **SP2:** cross-stage columnar fusion pass (fuse adjacent `Scan`/`Map`), proven faster
  + byte-identical against #1's equivalence net.
- **SP3:** DCE, projection/predicate pushdown, CSE passes.
- **SP4:** emit backends (SQL, DataFusion/Substrait) reusing the IR.
- Eventually: profile-once speculative compilation for full-pipeline fusion + AOT emit.
