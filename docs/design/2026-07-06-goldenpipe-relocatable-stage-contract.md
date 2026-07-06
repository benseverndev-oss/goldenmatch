# GoldenPipe relocatable-stage contract — the groundwork for cross-process / cross-language

**Status:** design (no code). **Owner:** ER platform. **Created:** 2026-07-06.
**Follows:** `2026-07-06-goldenpipe-orchestrator-pivot.md` (framing) and
`2026-07-06-goldenpipe-stage0-findings.md` (measurement).

## Why this doc exists

Stage 0 measured the single-process pipeline and found the between-stage handoff
is **0.2% of the wall** (the wall is ~99% kernel compute, `goldenmatch.dedupe`
alone ~75%). Conclusion: **do not build a streaming executor now — it optimizes
the wrong 1%.**

But that is a statement about *today's* single-process workload, not about the
*architecture*. The value of pillar 2 is real in the future — **out-of-core**
(data > memory) and **cross-process / cross-language** pipelines — where a stage
boundary stops being a free in-process reference pass and becomes real
serialization. This doc specifies the **groundwork that keeps that door open**: a
stage contract that is *relocatable*. It is deliberately a contract + design, not
an executor. Building any execution machinery stays gated on a per-scenario
Stage-0 baseline (see Non-goals).

## The one principle everything else follows from

**Arrow-*capable*, not Arrow-*mandatory*.** A stage becomes relocatable when its
boundary can be expressed as Arrow (a shareable, cross-language format) instead of
an in-process Python object (a Polars `DataFrame` handed to a Python function).
But forcing an Arrow round-trip at every boundary *in the in-process path* would
add materialization to the 99%-compute wall to optimize a 0.2% handoff — a
regression to serve a future that isn't here yet. So:

> The stage boundary is **Arrow-representable**; the in-process path **never
> serializes** — the DataFrame passes by reference, exactly as today. The Arrow
> seam materializes **only** when a stage actually crosses a process / language /
> engine line.

This is the same discipline GoldenCheck's dict-array fast path used: exploit the
format when it's load-bearing; don't manufacture it when it isn't.

## What is already correct (don't rebuild it)

- **The planner is already language-agnostic.** `goldenpipe-core`'s `resolve.rs`
  emits an `ExecutionPlan` from a JSON `PipelineConfig` + per-stage
  `produces/consumes/needs`; it is exposed identically to Python (`goldenpipe-native`)
  and TS/WASM (`goldenpipe-wasm`), proven byte-identical by golden vectors.
  **Placement is orthogonal to planning** — the plan says *what runs in what
  order*; it does not care *where* a stage runs. So the plan needs **no change**.
- **`StageInfo` already declares the data contract** (`produces` / `consumes`
  artifacts). Relocation adds a *placement* dimension, not a new dependency model.

## The contract (design sketch — illustrative, not final API)

Three additions, each backward-compatible and inert on the in-process fast path.

### 1. A `Frame` handle at the stage boundary
Today `PipeContext.df: pl.DataFrame` and stages do `_transform(ctx.df)` →
`ctx.df = result.df`. Introduce a thin handle that is Arrow-representable but
DataFrame-backed in-process:

```python
class Frame(Protocol):
    def polars(self) -> pl.DataFrame: ...           # in-process: returns the backing df BY REFERENCE (no copy)
    def arrow_batches(self) -> Iterator[pa.RecordBatch]: ...   # lazy; only a boundary-crossing adapter calls this
    @classmethod
    def from_arrow(cls, batches: Iterable[pa.RecordBatch]) -> "Frame": ...

class LocalFrame(Frame):   # the only impl needed for Phase A
    # wraps a pl.DataFrame; .polars() is identity; .arrow_batches() lazily .to_arrow()s
```

In-process stages keep calling `.polars()` — zero conversion, Stage-0 numbers
unchanged. A *remote* stage adapter is the only caller of `.arrow_batches()` /
`.from_arrow()`. `ctx.df` stays as a compatibility alias over `frame.polars()`.

### 2. A stage-*location* field
A stage's home is a config detail the router already has the shape to honor:

```python
class StageInfo:
    ...
    location: Literal["local", "remote"] = "local"   # + an engine/transport ref when remote
```

`local` → today's in-process Python adapter (unchanged). `remote` → a
`RemoteStage` adapter that ships `frame.arrow_batches()` to the target engine and
reads the result back as Arrow. The `Runner` dispatches on `location`; the
`ExecutionPlan` is untouched.

### 3. A transport seam (defined, not built)
"Remote" is not one thing — the right transport depends on the boundary:
- **In-process FFI** (another language, same process) → Arrow **C Data Interface**
  (zero-copy pointer handoff — the exact mechanism the `*-core` kernels already
  speak).
- **Cross-process, same host** → Arrow **IPC stream** over a pipe / shared memory.
- **Another engine** (DuckDB / Postgres / a TS worker) → the engine's Arrow ingress
  (these already have Arrow-native surfaces — see the goldencheck/goldenflow SQL
  work), or Arrow **Flight** for networked.
The contract names the seam and the selection axis; no transport is implemented
here.

## Staged rollout (each phase gated on its own Stage-0 baseline)

- **Phase A — the seam, inert (safe to do anytime).** Introduce `Frame`/`LocalFrame`
  + the `location` field with only the `local` path wired. Deliverable: prove
  `stage0_handoff_profile.py` numbers are **unchanged** (no forced conversion).
  This is "groundwork done correctly" — the door is open, nothing is built behind
  it. *Cost: small, non-regressing.*
- **Phase B — out-of-core (when a >memory workload exists).** A streaming `Frame`
  impl that yields record batches without holding the full frame; stages opt into
  batch iteration. Gate: a Stage-0 baseline on a genuinely out-of-core dataset
  showing the full-frame materialization is the bottleneck.
- **Phase C — cross-process / cross-language (when a relocated stage exists).** A
  `RemoteStage` over one transport, driving one real remote stage (e.g. a DuckDB
  transform) via the same plan. Gate: a measured workload where the remote stage's
  compute + Arrow serialization beats running it locally.

## Non-goals / guardrails (these are load-bearing)

1. **No executor now.** Stage 0 stands: the single-process handoff is not a
   bottleneck. Phase A is a contract, not an engine.
2. **No forced Arrow in-process.** The in-process path must stay a by-reference
   DataFrame pass. Any design that round-trips DataFrame↔Arrow on the local path
   is wrong by construction (it regresses the 99%-compute wall).
3. **Every build phase gated on its own baseline.** "It's groundwork for the
   future" is not a license to build speculatively — each phase past A must first
   measure that its target boundary (out-of-core / remote) is actually the cost.
4. **Pillar-4 discipline.** The Python `Runner` stays the reference; a relocated
   stage must produce byte-identical output to running it locally before the local
   path is considered replaceable.

## Open questions

- **Placement policy:** who decides a stage is `remote` — explicit config, or an
  auto-policy on data size / engine availability? (Start explicit; auto is a later,
  measured decision.)
- **Partial / streaming results:** the `Frame` contract for stages that emit
  batches incrementally (out-of-core) vs the whole-frame stages today — does the
  `ExecutionPlan` need a "streaming" annotation, or is that purely a `Frame`-impl
  concern? (Leaning: `Frame`-impl concern; the plan stays about ordering.)
- **Artifact handoff:** stages also pass non-`df` artifacts (`findings`, `profile`,
  `manifest`, `clusters`) via `ctx.artifacts`. Which of these need an
  Arrow/serializable contract for a remote stage, and which stay local-only
  metadata? (Scope per stage when Phase C targets it.)

## Reference paths

- Runtime seam: `packages/python/goldenpipe/goldenpipe/{models/context.py (PipeContext.df),
  engine/runner.py (dispatch), adapters/*.py (the stage impls), models/stage.py (StageInfo)}`
- Planner (unchanged): `packages/rust/extensions/goldenpipe-core/src/{resolve.rs, model.rs}`
- Baseline harness: `packages/python/goldenpipe/benchmarks/stage0_handoff_profile.py`
