# goldenpipe-core: real dependency-DAG planner contract

**Date:** 2026-07-04
**Status:** Design (approved for spec review)
**Depends on:** SP1 (`goldenpipe-core` planner crate, MERGED #1418), SP2 (Python parity gate #1424), SP3 (TS/WASM reroute + parity gate #1427). This is the first *contract-hardening* slice on top of the source-of-truth the SP1–SP3 program established.

---

## 1. Goal

Turn the planner from "validate a manually-ordered linear list" into a **dependency-graph-aware resolver** that activates the currently-dead `needs` field, reorders minimally to satisfy declared dependencies, and detects the failure classes the linear model cannot (cycles, missing producers, ambiguous co-production, unknown `needs`). The Rust `goldenpipe-core` crate is the reference; Python and TS pure resolvers re-conform to it, enforced byte-identical by the existing SP2/SP3 parity gates.

This hardens the single source of truth *before* stacking features (conditional stages, fan-out, richer routing) on top — each of those assumes a real graph underneath.

---

## 2. The load-bearing constraint: artifact re-production

`goldenflow.transform` produces `["df", "manifest"]` and **consumes `["df"]`** — it *re-produces* `df`. Every real pipeline does this (a transform mutates the frame). So `df` has multiple producers (`load`, `transform`, …). Pure topological-sort-from-artifacts is therefore **underspecified**: given two producers of `df` and a downstream consumer, the artifact graph alone cannot say which `df` the consumer receives.

Today, **config order silently disambiguates**: `transform` is listed after `load`, `dedupe` after `transform`, so each consumer sees the most-recent `df`. This design keeps config order as the authoritative sequence *for that reason* and layers graph validation + minimal reordering on top — it does NOT attempt a from-scratch topological sort that would lose re-production disambiguation.

**Terminology:** a *re-production chain* is a sequence of stages each of which both consumes and produces artifact X (e.g. `load`→`df`, `transform` consumes+produces `df`). Chains are legal and ordered by config order. *Unordered co-production* is two stages producing X where neither consumes the other's chain and no `needs`/dependency orders them — that is the ambiguity this contract now rejects.

---

## 3. The hardened contract

### 3.1 Ordering: config-order-authoritative + minimal reorder

`resolve` keeps the config's stage order as the base sequence, and reorders a stage **only** to satisfy a *declared dependency* that config order violates:

1. **Explicit `needs`**: `spec.needs` (a list of stage names/keys) means "these stages must run before me." Currently declared and ignored; now enforced as must-precede edges.
2. **Sole-producer artifact dependency**: if a stage consumes artifact X and exactly one stage in the pipeline produces X (and it's listed later), that producer must precede the consumer.

Reordering is the minimal permutation that satisfies all declared edges while preserving config order everywhere the edges don't constrain it (a **stable** topological sort keyed by original config index). Concretely: an already-valid, correctly-ordered pipeline resolves **byte-identically** to today; a pipeline whose only problem was listing a consumer before its sole producer now resolves instead of erroring.

**`load` auto-prepend** is unchanged (SP1 behavior): a stage registered under key `load` is prepended as the literal name `load`; else `df` is seeded. `load` participates in the graph as the initial `df` producer.

### 3.2 Re-production disambiguation (unchanged semantics, now explicit)

For an artifact with a re-production chain, a consumer depends on the **most recent preceding producer in config order** (the current silent behavior, now a defined rule). This is why config order stays authoritative: it *is* the chain order. Reordering never breaks a chain — a re-producer that also consumes X is pinned after the prior producer by rule 2 above.

### 3.3 New validation — `PlanError` variants

The tagged `PlanError` enum gains variants (keep `UnknownStage`; `Wiring` is generalized/renamed — see migration):

- **`MissingProducer { stage, artifact }`** — a consumed artifact that **no** stage in the pipeline (nor the `df` seed / `load`) produces. Replaces today's `Wiring` "no prior stage produces it" for the *truly absent* case (distinct from merely mis-ordered, which now reorders instead).
- **`AmbiguousProducer { artifact, producers }`** — two or more stages produce `artifact` that are **not** in a single re-production chain and are **not** ordered by `needs`/dependency, and a consumer depends on `artifact`. The real re-production bug, previously silent last-writer-wins.
- **`Cycle { stages }`** — the declared dependency edges (`needs` + sole-producer deps) contain a cycle; `stages` lists the members in a deterministic order for the message.
- **`UnknownNeed { stage, needs }`** — a `spec.needs` entry naming a stage/key not present in the resolved pipeline.
- **`UnknownStage { use }`** — unchanged (a `use` with no registered stage).

`needs` contradicting config order is **not** an error: `needs` is the stronger signal, so the planner reorders to satisfy it (only a true cycle errors).

### 3.4 Determinism

Byte-parity across surfaces requires a single canonical output for any input. The topological sort is **stable**: ties (stages with no ordering constraint between them) preserve original config index; error variants that carry lists (`Cycle.stages`, `AmbiguousProducer.producers`) emit them in a deterministic order (config index, then key). No hash-map iteration order leaks into output (the SP1 `preserve_order` / `BTreeSet` discipline continues).

---

## 4. Architecture / components

### 4.1 `goldenpipe-core` (the reference — `resolve.rs`)

Rewrite `resolve()` from the linear scan to:
1. Build the stage list (config order, with `load` prepended per SP1).
2. Build the dependency edge set: for each stage, `needs` edges + sole-producer artifact edges (skip artifacts with a re-production chain — those are ordered by config-order rule 3.2; skip multi-producer artifacts that resolve to a chain).
3. Validate: unknown `needs` → `UnknownNeed`; missing producer → `MissingProducer`; unordered co-production feeding a consumer → `AmbiguousProducer`.
4. Stable topological sort (Kahn with a min-heap keyed by config index); a remaining cycle → `Cycle`.
5. Emit `ExecutionPlan` (the same `PlannedSpec` shape as SP1 — no model change to the output row; ordering is the only behavior change).

`model.rs`: extend the `PlanError` enum with the new variants (tagged union, `#[serde(tag="kind", rename_all="snake_case")]`, matching SP1's discriminant style). `StageSpec.needs` already exists — it just becomes read.

### 4.2 `json.rs` + golden vectors

`resolve_json` is unchanged in shape (the `ok`/`err` envelope). New golden-vector cases added to `tests/vectors/resolve.json`: a needs-driven reorder, a sole-producer reorder, a re-production chain (byte-identical to config order), a missing-producer error, an ambiguous-producer error, a cycle error, an unknown-need error. These vectors are the cross-surface contract SP2/SP3 replay.

### 4.3 Python re-conform (`resolver.py`)

Rewrite `Resolver.resolve` to the same algorithm. The SP2 parity gate (`_planner_json.py` → the `resolve` vectors) enforces byte-parity. `WiringError` gains subclasses / a `kind` discriminant OR the shim maps the richer errors — the additive-attrs pattern from SP2 is extended (message-compatible; existing consumers reading `str(e)` / `except WiringError` keep working; the shim emits the new `{err:{kind:...}}` shapes).

### 4.4 TS re-conform (`resolvePure` in `resolver.ts`)

Rewrite `resolvePure` (the SP3 pure core) to the same algorithm. The SP3 Leg A gate (`plannerJsonPure.ts` → the `resolve` vectors) enforces byte-parity; Leg B (wasm == vectors) re-validates against the rebuilt core. The reroute (`resolveViaWasm`) is unaffected in shape — it already round-trips the `ok`/`err` envelope; it gains handling for the new `err.kind`s in `throwFromErr` (they map to a raised error; unknown kinds already throw).

### 4.5 Host consumers (unchanged)

The Runner loop, adapters, CSV, Reporter, MCP/CLI surfaces are untouched — they consume `ExecutionPlan.stages` (same shape) and the raised errors (message-compatible). `list_stages`/`explain` surfaces already print `produces`/`consumes`; they can additionally surface `needs` (optional, not required for this slice).

---

## 5. Error handling

- Every new failure is a typed `PlanError` variant with a deterministic message; the pure fallbacks raise message-compatible errors so existing `except`/`catch` sites are unaffected.
- The reroute's `throwFromErr` (SP3) already throws on unrecognized `kind`; it gains explicit branches for the new kinds (mapped to a `WiringError` or a plain planner error carrying the structured fields).
- No panics / unwraps on malformed input: the JSON `parse` guard (SP1) stays; graph construction returns `PlanError`, never panics.

---

## 6. Testing

- **Rust unit tests** (`resolve.rs`): reorder-by-needs, reorder-by-sole-producer, re-production chain stays config-order, byte-identical-to-old for already-valid pipelines (regression pin), each new error variant, determinism (same input → same output across repeated runs; tie-break by config index).
- **Golden vectors** (`resolve.json`): the cross-surface cases in §4.2 — replayed by Rust (`golden_vectors.rs`), Python (SP2 Leg), TS (SP3 Leg A + Leg B).
- **Regression guarantee:** a vector asserting an existing valid 3-stage pipeline resolves byte-identically to the pre-change output (the migration invariant "already-valid → unchanged").
- Box discipline: Rust on-box (`cargo test`/`fmt`/`clippy`); Python parity on-box via the SP2 runner; TS CI-only (vitest OOMs the box).

---

## 7. Migration / compatibility

- **Already-valid, correctly-ordered pipelines:** byte-identical output. (Pinned by a regression vector.)
- **Mis-ordered-but-valid pipelines** (consumer before its sole producer): previously a `Wiring` error, now resolve correctly. Strictly better; no user action.
- **Genuinely ambiguous pipelines** (unordered co-production feeding a consumer): previously silent last-writer-wins, now an `AmbiguousProducer` error. A caught latent bug — the only "breaking" case, and it was already wrong.
- **`needs`:** previously ignored; now enforced. Any config that set `needs` inconsistently with a working order was relying on it being ignored — now it either agrees (no change) or reorders/errors (surfacing a real inconsistency).
- Error-message wording changes for the wiring case (now `MissingProducer`), but consumers read `.message`/catch the type, not exact text; the parity vectors freeze the new wording.

---

## 8. Scope / non-goals

**In scope:** the ordering algorithm, `needs` activation, the four new error variants, determinism, and the three-surface re-conform + vectors.

**Out of scope (future slices, now unblocked by the real graph):** conditional/optional stages beyond `skip_if`; fan-out/parallel execution (this is a *planning* contract, execution stays sequential in the host Runner); typed/schema'd artifacts; `on_error` retry/fallback; positional `insert`. Explicitly NOT touching the Runner loop, adapters, or IO (orchestration stays a per-language host).

---

## 9. Graduation

- Rust `resolve.rs` topological + validation, on-box `cargo test`/`fmt`/`clippy` clean.
- New golden vectors added; Rust `golden_vectors.rs` green.
- Python `resolver.py` re-conformed; SP2 parity gate green (pure-Python == core on all resolve vectors incl. the new ones).
- TS `resolvePure` re-conformed; SP3 Leg A + Leg B green (pure-TS == wasm == core).
- Regression vector proves already-valid pipelines unchanged.
- No perf gate (planner, no hot loop — consistent with SP1–SP3; the sort is over ~5 stages).

Outcome: the planner contract models the dependency graph it always had the data for; `needs` is real; the failure classes the linear model hid are now typed errors — and all three surfaces are provably locked to the hardened core.
