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

`goldenflow.transform` produces `["df", "manifest"]` and **consumes `["df"]`** — it *re-produces* `df`. So across a real pipeline `df` has multiple producers (`load`, `transform`, …). Pure topological-sort-from-artifacts is therefore **underspecified**: given two producers of `df` and a downstream consumer, the artifact graph alone cannot say which `df` the consumer receives.

Today, **config order silently disambiguates**: `transform` is listed after `load`, `dedupe` after `transform`, so each consumer sees the most-recent `df`. This design keeps config order as the authoritative sequence *for that reason* and layers graph validation + minimal reordering on top — it does NOT attempt a from-scratch topological sort that would lose re-production disambiguation.

**The seed (resolves the load-vs-consume asymmetry).** The input frame `df` exists *before* any stage runs. Model it as a virtual **seed** producer sitting at position −1. The seed:

- **satisfies** a consumer's need for `df` (so a stage consuming `df` at the head of the pipeline never raises `MissingProducer`), but
- is **not a stage**, so it never counts toward the multi-producer / `AmbiguousProducer` tests, which range over *stages* only.

When a `load` stage is present it is auto-prepended (SP1 behavior, unchanged) and becomes the real position-0 `df` producer; when absent, the seed provides `df`. Either way `df` is available at position 0. This is why the flagship `load → transform → dedupe` pipeline is *not* ambiguous even though `load` produces `df` and `transform` re-produces it: the chain head (seed or `load`) produces `df` **without consuming it**, and every downstream re-producer both consumes and produces `df` in config order — a legal, ordered re-production chain, not unordered co-production.

**Terminology:** a *re-production chain* is a sequence of stages ordered by config order, each of which (after the head) both consumes and produces artifact X. Chains are legal. *Unordered co-production* is two or more **stages** producing X where an unsatisfied consumer of X cannot tell which one to bind to and no `needs` edge orders them — that is the ambiguity this contract now rejects (see the precise predicate in §3.1).

---

## 3. The hardened contract

### 3.1 Ordering — the algorithm (deterministic, byte-identical across surfaces)

Because the chain-vs-ambiguity question is *the* load-bearing predicate and must be byte-identical across Rust/Python/TS, the contract is specified as one algorithm, not prose rules.

Let `S = [s_0 … s_{n-1}]` be the stages in **config order** (with `load` auto-prepended per SP1, so `load` is always index 0 when present). Define the **config-order availability set**:

```
AVAIL(i) = SEED ∪ ⋃_{j < i} produces(s_j)
```

where `SEED = {"df"}` when no `load` stage is present, else `{}` (in which case `load` at index 0 produces `df`).

**Identifier space (M1):** `needs` entries, and the stages referenced by produce/consume reasoning, are **matched** by **registry key** — i.e. the stage's `use` string, the same identifier SP1 already resolves stages by (`registry.py` keys by `key`, which can differ from `info.name`). `UnknownNeed` matches against this key space.

**Error `stage` display field (MED1).** Distinct from the matching key above: the `stage` field *carried in an error payload* uses the **planned name** (`spec.name or info.name`), matching the current `Wiring` error exactly (resolve.rs emits `stage=name`; the name is also embedded in the message text). Key and name legitimately differ (a vector exercises `spec.name="alias"`). Keeping the error `stage` as the planned name preserves message-compatibility (§5) and means the *only* payload change in the `Wiring`→`MissingProducer` rename is dropping `available` (§7). Denote this `pname(s_i)` below.

**Edge set.** Build the set of must-precede edges `(a → b)` ("a runs before b"):

1. **`needs` edges.** For each `s_i` and each entry `n` in `needs(s_i)`:
   - no stage has key `n` → `UnknownNeed { stage: pname(s_i), needs: [n] }`;
   - `n == key(s_i)` (self-need) → contributes a self-edge, caught as `Cycle` in step 4;
   - otherwise add edge `(stage-with-key-n → s_i)`.
   - Duplicate `needs` entries collapse to one edge (idempotent).

2. **Sole-producer edges (guarded — the B2 fix).** For each `s_i` and each `X` in `consumes(s_i)`:
   - **If `X ∈ AVAIL(i)`** → already satisfied by config order: **no edge, no error.** This is what keeps an already-valid pipeline byte-identical to today — a consumer whose artifact is provided by the seed or any earlier stage is never reordered.
   - **Else** (`X ∉ AVAIL(i)`) let `L(X) = { s_j : j > i and X ∈ produces(s_j) }` — the *later* producers (stages only; the seed is never in `L`):
     - `|L(X)| = 0` → `MissingProducer { stage: pname(s_i), artifact: X }` (no stage anywhere produces `X`, and the seed does not provide it — the truly-absent case).
     - `|L(X)| = 1` → add edge `(that producer → s_i)` (the minimal reorder: the sole later producer moves ahead of its consumer).
     - `|L(X)| ≥ 2` → `AmbiguousProducer { artifact: X, producers: [keys of L(X) in config-index order] }`, **unless** the `needs` edges fix **exactly one** member of `L(X)` to precede `s_i` (then use that one and add no ambiguity error). If `needs` pins **two or more** members of `L(X)` before `s_i`, it stays `AmbiguousProducer` — a deliberate conservative rejection (the config is under-specified even though Kahn would resolve a concrete order; the author should say which one binds). This is the precise, algorithmic chain-vs-ambiguity predicate (M3): ambiguity is exactly "an unsatisfied consumer with ≥2 candidate later producers and not exactly one `needs`-pinned tiebreak."

   **Soundness (monotonicity):** a sole-producer edge only ever pulls a producer *earlier*. Pulling a producer earlier can only *add* artifacts to an earlier position; it can never remove an artifact from a later consumer's availability. So computing violations against config-order `AVAIL` once, then reordering, is sound — no already-satisfied consumer's *availability* becomes unsatisfied. (This availability argument is about sole-producer edges only; a `needs` edge is authoritative and may re-order/re-bind an already-satisfied re-production consumer on purpose — see §3.2.)

   **First-violation short-circuit (MIN1).** When several violations coexist, `resolve` returns the **first** one and does not collect: by phase order (`UnknownStage` during list-build → `UnknownNeed` step 1 → `MissingProducer`/`AmbiguousProducer` step 2 → `Cycle` step 4), then within a phase by config index of the erroring stage, then by that stage's `consumes` order. This ordering is part of the byte-parity contract.

3. **(reserved)** — no further edge sources in this slice.

4. **Stable topological sort.** Kahn's algorithm with a min-heap keyed by original config index; ties (two stages with no edge between them) preserve config order. Any remaining cycle in the edge set (`needs` + sole-producer, including self-edges) → `Cycle { stages: [members in config-index order] }`.

**Regression invariant:** an already-valid, correctly-ordered pipeline produces **zero** edges → the stable sort returns config order unchanged → output is **byte-identical to today**.

### 3.2 Re-production disambiguation (unchanged semantics, now explicit)

A consumer of an artifact with multiple producers binds to the **most-recent preceding producer in the resolved order**. For pipelines that use **no `needs`**, this is byte-for-byte the current silent behavior: the only edges are sole-producer edges, each pull-forward only *adds* availability earlier (the §3.1 monotonicity argument), so it never crosses an already-satisfied consumer, and config order — hence every re-production binding — is preserved. A re-producer that also consumes X is naturally pinned after the prior producer because its own `consumes(X)` was already satisfied in config order (`X ∈ AVAIL(i)`) → no edge → it never jumps ahead of its predecessor in the chain.

**`needs` is the exception (MED2).** A `needs` edge is authoritative and can pull a re-producer **across an already-satisfied consumer**, re-binding it. Example: config `[P1_x, C_x, P2_x]` where P1 and P2 both produce `x` and `C_x` (which consumes `x`, already satisfied by P1) declares `needs=[P2]`; Kahn yields `[P1, P2, C_x]`, so `C_x` now binds P2's `x`, not P1's. This is deliberate ("`needs` is the stronger signal", §3.3) and covered by the §7 migration — so re-production semantics are "unchanged" only for pipelines that do not use `needs`; a config that adds `needs` opts into exactly this re-binding.

### 3.3 New validation — `PlanError` variants

The tagged `PlanError` enum gains variants; `UnknownStage` is unchanged; **`Wiring` is renamed to `MissingProducer` and its payload changes** (see migration):

- **`MissingProducer { stage, artifact }`** — a consumed artifact that **no** stage in the pipeline (nor the `df` seed / `load`) produces. Replaces today's `Wiring` "no prior stage produces it" for the *truly absent* case (distinct from merely mis-ordered, which now reorders instead). **Drops the old `available` field** that `Wiring` carried.
- **`AmbiguousProducer { artifact, producers }`** — an unsatisfied consumer of `artifact` has ≥2 later stage-producers and no `needs` tiebreak (§3.1 rule 2). The real re-production bug, previously silent last-writer-wins.
- **`Cycle { stages }`** — the declared dependency edges (`needs` + sole-producer, incl. self-need) contain a cycle; `stages` lists the members in config-index order.
- **`UnknownNeed { stage, needs }`** — a `spec.needs` entry naming a stage/key not present in the resolved pipeline.
- **`UnknownStage { use }`** — unchanged (a `use` with no registered stage).

`needs` contradicting config order is **not** an error: `needs` is the stronger signal, so the planner reorders to satisfy it (only a true cycle errors).

### 3.4 Determinism

Byte-parity across surfaces requires a single canonical output for any input. The topological sort is **stable**: ties preserve original config index (`load` = index 0). Error variants that carry lists (`Cycle.stages`, `AmbiguousProducer.producers`) emit them in config-index order. No hash-map iteration order leaks into output (the SP1 `preserve_order` / `BTreeSet` discipline continues).

---

## 4. Architecture / components

### 4.1 `goldenpipe-core` (the reference — `resolve.rs`)

Rewrite `resolve()` from the linear scan to the §3.1 algorithm:
1. Build the stage list (config order, with `load` prepended per SP1).
2. Compute `SEED` and, for each stage, the config-order `AVAIL(i)`.
3. Build the edge set: `needs` edges (→ `UnknownNeed` on unknown) + guarded sole-producer edges. Classify each unsatisfied consumer per §3.1 rule 2 (→ `MissingProducer` / edge / `AmbiguousProducer`).
4. Stable topological sort (Kahn with a min-heap keyed by config index); a remaining cycle → `Cycle`.
5. Emit `ExecutionPlan` (the same `PlannedSpec` shape as SP1 — no model change to the output row; ordering is the only behavior change).

`model.rs`: extend the `PlanError` enum — rename `Wiring` → `MissingProducer { stage, artifact }` (drop `available`), add `AmbiguousProducer`, `Cycle`, `UnknownNeed` (tagged union, `#[serde(tag="kind", rename_all="snake_case")]`, matching SP1's discriminant style). `StageSpec.needs` already exists — it just becomes read.

### 4.2 `json.rs` + golden vectors

`resolve_json` is unchanged in shape (the `ok`/`err` envelope). Vector changes in `tests/vectors/resolve.json`:

- **Rewrite** the existing wiring case from `{"err":{"kind":"wiring","stage":…,"missing":…,"available":…}}` to `{"err":{"kind":"missing_producer","stage":…,"artifact":…}}` (the truly-absent case). This is the M2 payload change, not an addition.
- **Add** cases: a `needs`-driven reorder; a sole-producer reorder (consumer listed before its sole producer, now resolves); a re-production chain (byte-identical to config order); an `ambiguous_producer` error; a `cycle` error; an `unknown_need` error.
- **Add** a regression vector pinning an already-valid 3-stage pipeline to its exact current output (the "already-valid → unchanged" invariant).

These vectors are the cross-surface contract SP2/SP3 replay.

### 4.3 Python re-conform (`resolver.py` + `_planner_json.py`)

Rewrite `Resolver.resolve` to the §3.1 algorithm. The SP2 parity gate (`_planner_json.py` → the `resolve` vectors) enforces byte-parity.

- `resolver.py`: keep the **`WiringError` class name** for back-compat (existing `except WiringError` sites in `server.py`, `mcp/server.py`, `cli/main.py`, `test_resolver.py` keep catching it), but it now represents the missing-producer case and carries `.artifact` (the SP2 additive-attrs pattern extended). New failure classes (`AmbiguousProducer`, `Cycle`, `UnknownNeed`) are new exception types / a `kind` discriminant; message text is frozen by the vectors.
- `_planner_json.py` (SP2 shim): the resolve mapping currently emits `{"err":{"kind":"wiring","stage":e.stage,"missing":e.missing,"available":e.available}}`. **Rewrite it** to emit `{"err":{"kind":"missing_producer","stage":…,"artifact":…}}` (drop `available`) for the truly-absent case, and add branches for `ambiguous_producer` / `cycle` / `unknown_need`.

### 4.4 TS re-conform (`resolvePure` in `resolver.ts`)

Rewrite `resolvePure` (the SP3 pure core) to the §3.1 algorithm. The SP3 Leg A gate (`plannerJsonPure.ts` → the `resolve` vectors) enforces byte-parity; Leg B (wasm == vectors) re-validates against the rebuilt core. The reroute (`resolveViaWasm` in `plannerJson.ts`) already round-trips the `ok`/`err` envelope; `throwFromErr` gains explicit branches for the new `err.kind`s (`missing_producer`, `ambiguous_producer`, `cycle`, `unknown_need`) — unknown kinds already throw.

### 4.5 Host consumers (unchanged)

The Runner loop, adapters, CSV, Reporter, MCP/CLI surfaces are untouched — they consume `ExecutionPlan.stages` (same shape) and the raised errors (`except WiringError` still catches the renamed-in-spirit case; new kinds surface as their own errors). `list_stages`/`explain` surfaces already print `produces`/`consumes`; they can additionally surface `needs` (optional, not required for this slice).

---

## 5. Error handling

- Every new failure is a typed `PlanError` variant with a deterministic message; the pure fallbacks raise message-compatible errors (`WiringError` retained by name) so existing `except`/`catch` sites are unaffected for the missing-producer case, and the three genuinely-new failure classes raise their own errors.
- The reroute's `throwFromErr` (SP3) already throws on unrecognized `kind`; it gains explicit branches for the new kinds.
- No panics / unwraps on malformed input: the JSON `parse` guard (SP1) stays; graph construction returns `PlanError`, never panics.

---

## 6. Testing

- **Rust unit tests** (`resolve.rs`): reorder-by-needs, reorder-by-sole-producer, re-production chain stays config-order, already-satisfied-consumer-not-reordered (the B2 guard, explicit), byte-identical-to-old for already-valid pipelines (regression pin), each new error variant (`MissingProducer`, `AmbiguousProducer`, `Cycle`, `UnknownNeed`), determinism (same input → same output; tie-break by config index; list ordering).
- **Golden vectors** (`resolve.json`): the cross-surface cases in §4.2 — replayed by Rust (`golden_vectors.rs`), Python (SP2 Leg), TS (SP3 Leg A + Leg B).
- **Regression guarantee:** a vector asserting an existing valid 3-stage pipeline resolves byte-identically to the pre-change output.
- Box discipline: Rust on-box (`cargo test`/`fmt`/`clippy`); Python parity on-box via the SP2 runner; TS CI-only (vitest OOMs the box).

---

## 7. Migration / compatibility

- **Already-valid, correctly-ordered pipelines:** byte-identical output. (Pinned by a regression vector; guaranteed by the zero-edges property in §3.1.)
- **Mis-ordered-but-valid pipelines** (consumer before its sole producer): previously a `Wiring` error, now resolve correctly. Strictly better; no user action.
- **Genuinely ambiguous pipelines** (unsatisfied consumer with ≥2 later producers, no `needs` order): previously silent last-writer-wins, now an `AmbiguousProducer` error. A caught latent bug — the config was already relying on undefined behavior.
- **`needs`:** previously ignored; now enforced. Any config that set `needs` inconsistently with a working order was relying on it being ignored — now it either agrees (no change) or reorders/errors (surfacing a real inconsistency, possibly `Cycle` / `UnknownNeed`).
- **Breaking surface (honest list):** three *new* rejection classes did not exist before — `AmbiguousProducer`, `Cycle`, `UnknownNeed` — each fires only on a config that was already ill-defined or silently wrong. Plus the `Wiring` → `MissingProducer` rename drops the JSON `available` field (payload change frozen by the rewritten vector). Consumers read `.message` / catch the exception type (`WiringError` retained), not the exact JSON shape.

---

## 8. Scope / non-goals

**In scope:** the §3.1 ordering algorithm, `needs` activation, the `Wiring`→`MissingProducer` rename + three new error variants, determinism, and the three-surface re-conform + vectors.

**Out of scope (future slices, now unblocked by the real graph):** conditional/optional stages beyond `skip_if`; fan-out/parallel execution (this is a *planning* contract, execution stays sequential in the host Runner); typed/schema'd artifacts; `on_error` retry/fallback; positional `insert`. Explicitly NOT touching the Runner loop, adapters, or IO (orchestration stays a per-language host).

---

## 9. Graduation

- Rust `resolve.rs` topological + validation, on-box `cargo test`/`fmt`/`clippy` clean.
- New/rewritten golden vectors added; Rust `golden_vectors.rs` green.
- Python `resolver.py` + `_planner_json.py` re-conformed; SP2 parity gate green (pure-Python == core on all resolve vectors incl. the new ones).
- TS `resolvePure` re-conformed; SP3 Leg A + Leg B green (pure-TS == wasm == core).
- Regression vector proves already-valid pipelines unchanged.
- No perf gate (planner, no hot loop — consistent with SP1–SP3; the sort is over ~5 stages).

Outcome: the planner contract models the dependency graph it always had the data for; `needs` is real; the failure classes the linear model hid are now typed errors — and all three surfaces are provably locked to the hardened core.
