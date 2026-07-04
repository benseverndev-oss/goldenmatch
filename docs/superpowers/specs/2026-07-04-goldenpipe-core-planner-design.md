# goldenpipe-core — the pyo3-free planner kernel (SP1) — design

**Program:** move goldenpipe's duplicated Python↔TS planner logic onto one
pyo3-free Rust `goldenpipe-core` crate = **one source of truth**, and serve it to
Python (native wheel + fallback) and edge TS/WASM. **Goal is source-of-truth /
kill-drift, NOT speed** — the planner is not compute-bound, so the suite's
measure-first *perf* leg is explicitly waived; graduation gate is **byte-identical
parity + elimination of the live Python↔TS drift**, per the "one source of truth"
leg of the 2026-07-01 Rust-is-the-reference roadmap.

**Scope decisions (locked in brainstorming):** Python + edge TS surfaces ONLY (a
pipeline planner has no row-wise SQL meaning → DuckDB/PG dropped). Execution/IO
stays a thin per-language host. This spec is **SP1 = the crate + its golden-vector
parity harness only.** SP2 (Python binding) and SP3 (TS/WASM binding, where the
drift-kill lands) are separate specs.

## What is / isn't in the core (from reading the real code)

The engine splits cleanly:

**PURE / deterministic → `goldenpipe-core` (this crate):**
- **Resolver** (`engine/resolver.py|ts`) — `resolve(config, stage_info[]) →
  ExecutionPlan | WiringError`: auto-prepend the `load` stage when present,
  validate every stage's `consumes` is produced by an earlier stage (else
  `WiringError`), emit the ordered plan. Needs only stage **metadata**
  (`name/produces/consumes`), never executable stages — so it is cleanly pure.
- **Router** (`engine/router.py|ts`) — `apply_decision(decision, remaining) →
  remaining`: skip / abort / insert over the remaining **spec** list.
- **skip_if predicate** (the runner's `skipIf` falsy check) — Python `not artifact`
  and TS `isFalsy(...)` currently agree on all JSON types; the core pins ONE canonical
  predicate so they can't drift later.
- **auto_config** (`pipeline._auto_config` / TS equiv) — default stage selection
  from the available set (+ optional identity stage).
- **built-in decision predicates** (`decisions.py|ts`: severity_gate / pii_router
  / row_count_gate) — pure `ctx → Decision?`. NOTE (verified, and CORRECTED from an
  earlier draft): neither runner invokes these in its loop (both only apply a
  decision a *stage returns*); they are a reusable predicate library. The TS
  predicates already match Python **byte-for-byte on identical input** (`decisions.ts`
  tests the same `"critical"` / `"pii_detection"` strings). The TS no-op is caused by
  the upstream GoldenCheck-JS **check adapter** (host) never emitting those finding
  labels — that adapter stays in the host and is NOT touched here. So moving these into
  the core is a **no-op vs today's behavior**; the value is preventing FUTURE
  divergence of the predicate logic (one source of truth), NOT fixing the current
  adapter-caused no-op. Included because it completes the planner's pure surface and is
  cheap (~30 LOC); it is the weakest-value item and could be dropped without affecting
  the engine-critical drift-kill (which is Resolver + Router + auto_config).

**IO / side-effecting → STAYS the per-language host (NOT in the core):**
- **Runner loop** — executes stages (`stage.validate`/`run`, which call INTO
  goldencheck/goldenflow/goldenmatch; async in TS), times them, catches errors,
  and calls the core's `apply_decision` between stages. Arbitrary per-language
  code; cannot move.
- **Registry.discover** (entry-point / registration reflection), CSV load,
  Reporter, ctx mutation (writing `ctx.reasoning`/`ctx.timing`).

The host keeps the *when* (the loop); the core owns the *what* (plan + routing +
predicates), byte-identical across surfaces.

## Boundary types (serde structs; the JSON contract)

Mirrors the existing Python/TS models (`models/config.py`, `models/context.py`,
`models/stage.py`). Only the JSON-serializable subset crosses; `config_schema`
(a Python `type`) and the polars `df` never enter the core.

```rust
struct StageSpec   { name: Option<String>, use_: String /* serde rename "use" */,
                     needs: Vec<String>, skip_if: Option<String>,
                     on_error: OnError /* "continue"|"abort", default continue */,
                     config: JsonMap }
struct PipelineConfig { pipeline: String, source: Option<String>, output: Option<String>,
                        stages: Vec<StageEntry /* StageSpec | bare "use" string */>,
                        decisions: Vec<String> }
// `key` = the REGISTRY key the config's `use` references (Python entry-point discovery
// keys by ep.name, TS by registration name); `name` = info.name (used for plan naming).
// These CAN differ, so the core must key lookups by `key`, not `name`.
struct StageInfo   { key: String, name: String, produces: Vec<String>, consumes: Vec<String> }   // config_schema omitted
struct Decision    { skip: Vec<String>, abort: bool, insert: Vec<String>, reason: String }
struct PlannedSpec { name: String, use_: String, config: JsonMap,
                     skip_if: Option<String>, on_error: OnError }   // the executable-name + host-fillable slot
struct ExecutionPlan { stages: Vec<PlannedSpec> }
// Tagged error union — preserves the TWO existing error classes byte-faithfully:
//   Wiring        = a consume not produced by an earlier stage (today's WiringError)
//   UnknownStage  = a `use` with no registered stage (today a KeyError -> "resolution failed")
enum PlanError { Wiring { stage: String, missing: String, available: Vec<String> },
                 UnknownStage { use_: String } }
struct CtxSubset   { artifacts: JsonMap, metadata: JsonMap }                         // decision inputs only
struct ApplyResult { remaining: Vec<PlannedSpec>, router_note: Option<String> }      // note = exact ctx.reasoning["_router"] string
```

`StageEntry` handles the `stages: list[StageSpec | str]` union (a bare string = 
`StageSpec{use: s}`) — the `makeStageSpec` normalization, done ONCE in the core.

`JsonMap` = `serde_json::Map` with **`preserve_order`** (IndexMap, insertion order) —
NOT the default `BTreeMap` (sorted). Passthrough `config` maps are echoed into the
output and must round-trip in the SAME insertion order that Python `json.dumps` and JS
`JSON.stringify` emit, or the SP2/SP3 byte-parity gate flaps. See Deps.

## API (typed fns + JSON wrappers)

Typed Rust fns over the structs, plus `*_json(&str) -> String` wrappers (the shim +
golden-vector surface, matching goldengraph-core's JSON boundary):

1. `resolve(config, &[StageInfo]) -> Result<ExecutionPlan, PlanError>`
   — auto-prepend `load` iff a `StageInfo` with `key == "load"` is present (else seed
   `available = {"df"}`). For each stage entry: normalize (str→spec); look up its
   `StageInfo` **by `key == spec.use`** (NOT by `info.name` — they can differ). Missing
   lookup → `Err(PlanError::UnknownStage{use_})` (mirrors today's KeyError → "resolution
   failed"). Planned stage **name = `spec.name` else `info.name`** (matches
   `resolver.py:56-57`). Check `consumes ⊆ available`; missing → `Err(PlanError::Wiring{
   stage: name, missing, available: sorted})`. Push `PlannedSpec`, add `info.produces`.
   `available` is emitted **sorted** (both impls sort it: `resolver.py:63`,
   `resolver.ts:64`).
2. `apply_decision(&Decision, remaining: &[PlannedSpec]) -> ApplyResult`
   — abort → `{remaining: [], router_note: "ABORT: {reason}"}`; else skip
   (filter names ∈ decision.skip) then insert (prepend bare `PlannedSpec{name,
   use: name}` in original order); `router_note = reason` (or None). Host maps
   inserted names → stage objects and writes `router_note` into `ctx.reasoning`.
3. `evaluate_builtin(name: &str, ctx: &CtxSubset) -> Option<Decision>`
   — the canonical severity_gate / pii_router / row_count_gate over
   `artifacts.findings` / `metadata.input_rows`. Unknown name → None.
4. `auto_config(available: &[String], identity_opts: Option<JsonMap>) -> PipelineConfig`
   — the default `[goldencheck.scan, goldenflow.transform, goldenmatch.dedupe]`
   filtered to available, + `goldenmatch.identity_resolve` when `identity_opts` is
   **non-empty** and available (Python `if self._identity_opts` treats `{}` as
   not-given, so `Some(empty map)` → no identity stage — matches `pipeline.py:84`).
   **DIVERGENCE RESOLVED IN THE REFERENCE'S FAVOR (must be called out):** the TS
   `autoConfig` (`pipeline.ts:75-81`) has **no identity path at all** (TS `Pipeline`
   has no `identity_opts`). Canonicalizing to the Python-with-identity semantics means
   TS *gains* the identity stage when it adopts the core (a real TS behavior change,
   landing at SP3). This is the intended "resolve divergences in the reference's favor"
   move, not an accident — SP3's parity fixtures must encode the new TS behavior.
5. `skip_if_falsy(artifact: &JsonValue) -> bool`
   — one canonical predicate: falsy = `null | false | 0 | "" | [] | {}`, else truthy.
   Python `not artifact` and TS `isFalsy` **already agree on every JSON type** (no live
   divergence for JSON artifacts); unifying pins the semantics so they can't drift
   later. (Mirrors TS `isFalsy`, the more explicit spelling.)

## Error handling

- `resolve` returns `Result<ExecutionPlan, PlanError>`, never a panic. `PlanError` is
  the tagged union above and **preserves the two existing error classes**: `Wiring`
  (today's `WiringError` — consume not produced) and `UnknownStage` (today a `KeyError`
  → "Pipeline resolution failed"). The host maps each back to its language-native
  error, so behavior is byte-faithful. `resolve_json` returns `{"ok": ExecutionPlan}`
  or `{"err": PlanError}` (the `PlanError` tag distinguishes the two classes).
- Malformed JSON at the shim boundary is the shim's problem (SP2/SP3), not the
  core's; core `*_json` may return an `{"err": {"kind":"parse", ...}}` for
  robustness but the typed fns assume valid structs.
- No `unwrap` on external input; deterministic + panic-free is a crate invariant
  (a fuzz-ish golden-vector includes empty/degenerate inputs).

## Testing (SP1 — box-safe; `cargo test` links on NTFS D:, toolchain 1.94.0)

Rust unit tests + a **golden-vector fixture set** = the cross-surface parity
contract (SP2/SP3's fallbacks must reproduce these bytes). Fixtures live at
`packages/rust/extensions/goldenpipe-core/tests/vectors/*.json`
(`{fn, input, expected}`), replayed through the `*_json` wrappers:

- **resolve:** happy 3-stage order; auto-prepend `load`; bare-string stage entry;
  `PlanError::Wiring` (consume not produced) with sorted `available`;
  `PlanError::UnknownStage` (a `use` with no StageInfo); a `spec.name` override that
  differs from `info.name`; a `key != info.name` StageInfo (lookup by key);
  passthrough `config` echoed in insertion order; empty stages; a stage that both
  consumes+produces.
- **apply_decision:** skip; insert (order preserved); abort (router_note prefix);
  skip+insert combined; empty decision (no-op); insert-then-skip interplay.
- **evaluate_builtin:** severity_gate critical/none/empty-findings; pii_router
  hit/miss; row_count_gate <2 / ≥2 / missing input_rows; unknown name → null.
- **auto_config:** all available; subset available; +identity with opts; identity
  requested but unavailable.
- **skip_if_falsy:** null/false/0/""/[]/{} → true; 0.5/"x"/[0]/{"a":1}/true → false.

Determinism check: every `*_json` is idempotent + stable-ordered (serde_json with
sorted maps where the Python/TS emit sorted, e.g. the `available` set) so the
byte-parity gate doesn't flap. No `HashMap` iteration order in outputs.

## Crate + deps

- `packages/rust/extensions/goldenpipe-core/` (new workspace member — add to the
  root `Cargo.toml` members list). `edition = 2021`, pyo3-FREE.
- Deps: `serde` + `serde_json` with the **`preserve_order`** feature ONLY (no rayon,
  no arrow → trivially wasm32-clean for SP3, abi3-clean for SP2). `preserve_order` makes
  `serde_json::Map` an insertion-ordered `IndexMap` so passthrough `config` echoes in
  the same order Python/JS emit (the byte-parity requirement). The planner is ~200 LOC.
- `src/lib.rs` (crate doc = "single source of truth for the goldenpipe planner;
  native + wasm shims marshal JSON over these fns; pure-Python/TS planners are
  non-authoritative fallbacks that must reproduce these bytes"), `src/model.rs`
  (structs), `src/resolve.rs`, `src/router.rs`, `src/decisions.rs`,
  `src/config.rs` (auto_config + skip_if), `src/json.rs` (the `*_json` wrappers).

## Out of scope (SP1)

- SP2 (Python `goldenpipe-native` wheel + native-loader reference-mode + pure==core
  parity gate) and SP3 (TS/WASM reroute + the actual drift-kill + cross-surface
  fixtures) — separate specs.
- Touching the Runner / Registry / IO / Reporter (they stay host).
- The pure-Python and pure-TS planners keep working unchanged this slice (the core
  isn't wired into either yet — SP1 only proves the crate reproduces their logic on
  the golden vectors).
- Any perf work (there is no perf goal).
