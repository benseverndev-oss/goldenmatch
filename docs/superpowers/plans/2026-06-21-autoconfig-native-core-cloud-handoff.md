# Auto-config native core — cloud-session resume handoff

**Read this first, then continue the implementation plan.** Pick this up in a
**Claude cloud session on a `claude/*` branch** (this repo runs CI only on
`claude/*` PRs + `main` + the merge queue, and the cloud env is Linux with the
`wasm32` target + node toolchain — both of which the original dev box lacked).

- **Branch:** `feat/autoconfig-native-core` (PR #1159). Rebase/cherry-pick onto a
  `claude/*` branch if needed so CI fires.
- **Spec:** `docs/superpowers/specs/2026-06-20-autoconfig-native-core-design.md`
- **Plan:** `docs/superpowers/plans/2026-06-20-autoconfig-native-core.md` (Stages A-F)
- **Quality finding:** `docs/superpowers/specs/2026-06-21-autoconfig-sample-quality-finding.md`

## DONE (pushed)

- **A+B** `goldenmatch-autoconfig-core` crate: `decide_plan` (8-rule planner) +
  `classify_columns` (classifier). 91 unit tests.
- **C** Python binding: `goldenmatch-native` 0.1.6 JSON-in/out shims
  (`autoconfig_decide_plan` / `autoconfig_classify_columns`), default-OFF dispatch
  via `native_enabled("autoconfig")` (NOT in `_GATED_ON`) in
  `core/autoconfig_planner.py` + `core/autoconfig.py`, helpers in
  `core/autoconfig_native.py`. Pure-Python path unchanged (15 regression tests pass).
- **Golden harness:** `scripts/gen_autoconfig_golden.py` emits
  `packages/rust/extensions/autoconfig-core/golden/{planner,classifier}_vectors.json`
  (49 + 39 vectors) from the REAL pure-Python oracle. Rust `tests/golden.rs` asserts
  them (8 tests pass). These vectors are the **cross-surface parity gate** — TS must
  pass the SAME JSON.
- **D** measured (see finding): linear `extrapolate_to` under-estimates pair count by
  ~the sampling fraction; the real fix is native-fast full-frame measurement. Arrow
  path B intentionally NOT built (measure-first).
- **E1** `goldenmatch-autoconfig-wasm` crate: `#[wasm_bindgen]` shims
  (`autoconfig_decide_plan` / `autoconfig_classify_columns`, JSON in/out). Host
  `cargo check` passes; real wasm build not yet run.

## FIRST THING TO DO: validate A-C in CI

Once on a CI-getting branch, confirm the two gates the dev box couldn't run:
1. **clippy** on `autoconfig-core` (CI `rust` job: `cargo clippy --manifest-path
   autoconfig-core/Cargo.toml --all-targets -- -D warnings`). The crate was written
   clippy-aware but clippy NEVER actually ran locally — expect possibly a lint or two.
2. **native-ON parity** (CI `native` job runs `tests/test_autoconfig_native_parity.py`
   after `scripts/build_native.py` builds the 0.1.6 ext). This is the end-to-end proof
   of the Python binding; it SKIPS on a box without the ext.
Both are already wired into `.github/workflows/ci.yml` (the `rust` + `native` jobs).

## REMAINING TASKS

### E2 — TS WASM loader (`src/core/autoconfigWasm.ts`)
Build the wasm via wasm-pack (`--target web`), inline-base64 it, and load it
SYNCHRONOUSLY with wasm-bindgen's `initSync(bytes)` so the TS public API stays sync
(no `node:*` — edge-safe rule). Expose typed `decidePlan(...)` / `classifyColumns(...)`
that `JSON.stringify` in, call the wasm, `JSON.parse` out.

**Critical field-mapping (snake_case core JSON <-> camelCase TS):** the wasm returns
the serde shapes (`rule_name`, `pair_spill_threshold`, `chunk_size`, `max_workers`,
`clustering_strategy`; `col_type`, `null_rate`, `cardinality_ratio`, `avg_len`,
`needs_llm_escalation`). The TS interfaces are camelCase: `ExecutionPlan`
(`executionPlan.ts`: ruleName/pairSpillThreshold/chunkSize/maxWorkers/clusteringStrategy)
and `ColumnProfile` (`profiler.ts`: nullRate/cardinalityRatio/avgLength/inferredType/...).
Write an explicit snake->camel adapter both ways.

### E3 — Reroute TS planner/classifier through the wasm core (THE DEEP ONE)
This is NOT a simple "delete + call wasm." The TS port's vocabularies DIVERGE from the
core, so rerouting CHANGES TS behavior and WILL break TS tests — must be tsc + vitest
verified:
- **`ColumnType`** (`profiler.ts`) = 11 values using **`"id"`** (core: `identifier`),
  **`"text"`** (core: `string`), and it **LACKS `address` and `description`**. The core
  emits all 13. Decide: widen the TS `ColumnType` to the core's 13 (and map id<->identifier,
  text<->string), and update every downstream consumer that switches on col_type
  (TS blocking/matchkey selection) + the tests.
- **`BackendName`** (`executionPlan.ts`) has **no `"bucket"`**; the core does. In TS,
  `bucket_available` is always false (no native kernel), so the core returns
  `polars-direct` not `bucket` — but add `"bucket"` to the type for completeness/safety.
- **Planner rules:** TS `autoconfigPlannerRules.ts` has **6 rules; the core has 8**
  (adds `bucket_suggested` + the `no_rule_matched` fallback + duckdb-conditional). Routing
  through wasm makes TS match Python's 8 — update/replace the TS planner tests accordingly.
- **TS capabilities:** when building the `PlannerInput` caps for the wasm call, set
  `bucket_available=false, ray_available=false, ray_auto_select=false, user_backend=ctx`.
- Keep the public signatures of `applyPlannerRules` / the profiler functions STABLE so
  `autoconfigController.ts` and callers don't change; swap only the internals.
- Then DELETE the now-dead hand-written rule table + classifier heuristics.

### E3 parity test — `tests/parity/autoconfig-core.parity.test.ts`
Load the SAME `planner_vectors.json` / `classifier_vectors.json` (copy into
`tests/parity/fixtures/` via the existing emitter pattern), run the TS wasm path,
assert `== expected`. This is the cross-surface proof: Rust + Python + TS all green on
identical vectors. (Mind the snake/camel adapter when comparing.)

### E4 — TS build + CI
Add a wasm-pack build + base64-embed step to the TS build pipeline (tsup). Wire a
`wasm-pack` install + build into the CI TS lane, gated on
`packages/rust/extensions/autoconfig-{core,wasm}/**`. Confirm `tsc --noEmit && vitest run
&& build` green.

### F — gate-flip (own PR, after all green)
Add `"autoconfig"` to `_GATED_ON` in `core/_native_loader.py` (with a sign-off comment
like the clustering/block_scoring block) so native auto-config runs by default under
`GOLDENMATCH_NATIVE=auto`. Run the full Python + native + TS suites; confirm no diff in
committed configs on the regression fixtures. Republish `goldenmatch-native` 0.1.6 so
the wheel carries the new symbols (release step, per the stale-wheel footgun).

## Build/verify recipes (Linux cloud env)
- Rust core: `cargo test --manifest-path packages/rust/extensions/autoconfig-core/Cargo.toml`
  + `cargo clippy ... -- -D warnings`.
- Regenerate golden vectors (should be a no-op diff):
  `GOLDENMATCH_NATIVE=0 python scripts/gen_autoconfig_golden.py`.
- WASM: `wasm-pack build packages/rust/extensions/autoconfig-wasm --target web`.
- TS: `cd packages/typescript/goldenmatch && npx tsc --noEmit && npx vitest run`.

## Don't re-derive these (already settled)
- Serde null traps: `pair_spill_threshold` + `chunk_size` are `Option` -> JSON `null`
  (NOT `"none"`). `rule_name` always present.
- Classifier parity subtleties: two different cardinality denominators
  (`ColumnProfile.cardinality_ratio` uses total_rows; the guard uses len(values)+>=10);
  `_classify_by_data` branch order; email uses LAST `@` (`rfind`).
- Gate mechanism: reuse `native_enabled` + `_GATED_ON`, NOT a new env var.
