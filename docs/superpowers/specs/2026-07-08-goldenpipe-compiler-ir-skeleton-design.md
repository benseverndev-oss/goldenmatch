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
   existing machinery to produce the explicit config a stage would run, from the
   artifacts available at that point. Verified reuse points:
   - **Flow** — `transformer._apply_auto_transforms` derives ops purely:
     `profile_dataframe(df)` → per-column `select_transforms(col_profile)`. These are
     deterministic and are exactly what `transform_df(df)` runs, so re-deriving is
     byte-faithful; additionally every applied op is in `result.manifest.records`, so
     what actually ran is also readable post-hoc.
   - **Match** — the PRIMARY resolver is goldenpipe's own deterministic
     `match._build_config_from_contexts(column_contexts, df)` (builds an explicit
     `GoldenMatchConfig` from Check's `column_contexts`). The bare-auto
     `goldenmatch.auto_configure_df` is only the deeper FALLBACK (used when contexts are
     insufficient) and is separable but nondeterministic (see fidelity note).
   - **Check** — profiling/scan config from the resolved check set.
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
    if stage is explicitly configured:
        concrete = resolve(stage, ctx.artifacts)    # separable, deterministic
        nodes    = lower(stage.name, concrete)      # kernel: fine IR nodes
        execute_via_adapter(stage, ctx)             # delegating
    else:                                           # auto-configured stage
        execute_via_adapter(stage, ctx)             # run the auto path (== today)
        concrete = capture_from_run(stage, ctx)     # record what ACTUALLY ran
        nodes    = lower(stage.name, concrete)      # kernel: fine IR nodes (resolved=true)
    compiled.append(nodes)                          # accumulate the durable IR
    # ctx.artifacts updated by execution, feeding the next stage
```

Explicit stages resolve-then-execute; auto stages execute-then-capture (to avoid the
double-invocation nondeterminism above). Either way the IR ends up with fine nodes and
execution reproduces today's artifacts.

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
  Barrier   { raw_config }                       // any stage not modeled fine-grained
```

Only `load`/`goldencheck.scan`/`goldenflow.transform`/`goldenmatch.dedupe` lower to
fine-grained nodes in #1. Other real stages (`infer_schema` from the planner's
`confident_schema` rule, plus `identity`/`analysis`/`engine`) lower to a single opaque
`Barrier` node carrying the raw config; the delegating backend executes them via their
adapter. "compile==classic" for those rests entirely on delegating opaque-node
execution (no fine nodes, no future fusion) — acceptable for the skeleton.

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
names lower to a single opaque `Barrier` node carrying the raw config (forward
compatibility) rather than erroring.

The plan being lowered is the **Python runner's `ExecutionPlan`** (`engine/resolver.py`
— a list of `PlannedStage{name, config, ...}` the `Runner` executes), NOT the kernel's
`plan_pipeline` output (that is the upstream auto-config *brain* that decides which
stages to include — a different layer). `lower` keys on the planned stage's name/`use`.

## The delegating reference backend + auto-resolve fidelity

`execute` runs each stage's node-subgraph by **delegating to the existing engine**:
- For an **explicitly-configured** stage, delegate with the explicit config the nodes
  carry.
- For an **auto-configured** stage (`resolved: true`), delegate via the **auto path
  itself** (call the stage as it runs today) — NOT a reconstructed explicit config.
  This guarantees execution equals today's output trivially.

The delegating backend reuses the classic `Runner`'s per-stage `stage.run(ctx)` calls
(mutating `ctx.df`/`ctx.artifacts`), so it must also preserve the Runner's other
behaviors to stay faithful on non-trivial pipelines: `skip_if`, router decisions
(`result.decision → Router.apply`), `on_error` abort, and the relocatable-frame
materialization seam. #1's equivalence fixtures are straight-line load→check→flow→match
(no routers/skip_if), so the skeleton is safe reusing the Runner's loop directly; the
plan must either reuse that loop wholesale or explicitly scope these out so
"compile==classic" cannot silently diverge on a router/skip_if pipeline.

So in #1, the IR *records* the resolved concrete ops (for future passes to consume) but
execution does not yet depend on that record being a perfect explicit substitute.
**Fidelity is still gated:** the equivalence tests additionally assert that the recorded
resolved config matches what actually ran (so the IR is a faithful record, not just an
executable one). Making *fused* execution from the recorded config match auto is
sub-project 2's burden.

**Determinism hazard — record-what-ran, don't double-resolve.** The bare-auto Match
fallback (`auto_configure_df`) has cross-run memory (`GOLDENMATCH_AUTOCONFIG_MEMORY`,
default ON, keyed by data shape) and stratified-sample controller loops. If the staged
loop called `resolve` (auto-config #1) then `execute`'s auto path (auto-config #2), the
two invocations could diverge via the memory cache → the fidelity assertion flakes. So
for **auto-configured stages the node's recorded config is captured FROM the execution
that ran** (goldenflow `manifest.records`; goldenmatch `DedupeResult.postflight_report`
/ `_LAST_CONTROLLER_RUN`), i.e. resolve-after-execute — NOT a second independent
resolve. The equivalence gate also sets `GOLDENMATCH_AUTOCONFIG_MEMORY=0`. The primary
Match path (`_build_config_from_contexts`) is deterministic and separable, so #1's
fixtures exercise the contexts path and dodge the fallback's nondeterminism entirely.
This makes the pseudocode above `resolve` explicitly-configured stages before execute,
and capture auto stages' config after execute.

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
the recorded IR's resolved config matches what ran.

**Normalize nondeterministic fields before comparing.** goldenflow's
`Manifest.created_at` is a wall-clock `datetime.now(UTC)` and match stats carry timing
fields — a naive byte-compare of `manifest`/`match_stats` fails across two runs. The
gate normalizes/strips these (freeze `created_at`, exclude timing keys) so the compare
tests the data-carrying content. Env for the box: `GOLDENMATCH_NATIVE=0`,
`POLARS_SKIP_CPU_CHECK=1`, `GOLDENMATCH_AUTOCONFIG_MEMORY=0`. Tiny fixtures (<100k rows)
keep goldenmatch/polars fast and stay under the `confidence_required` threshold.

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
