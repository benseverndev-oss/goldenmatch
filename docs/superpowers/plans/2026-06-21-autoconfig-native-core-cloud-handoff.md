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
  `cargo check` + clippy pass; real `wasm-pack build --target web` now verified.
  Added `[package.metadata.wasm-pack.profile.release] wasm-opt = false` so the
  build is hermetic (binaryen download is blocked in network-restricted CI).

## DONE in the cloud session (2026-06-21, branch `claude/open-prs-review-xbvd93`)

- **A-C validated in CI's gates locally:** `cargo test` (91+8) + `cargo clippy
  -D warnings` on `autoconfig-core` now green. Clippy found exactly the predicted
  "lint or two" — two `doc-overindented-list-items` in `tests/golden.rs` (fixed by
  collapsing the multi-line `ColType` doc list). Golden-vector regen is a no-op diff.
- **E1 hardened:** `wasm-opt = false` (above) + `wasm-pack build --target web`
  verified producing a 1.28 MB wasm. `cargo check`/clippy on the wasm crate wired
  into the CI `rust` job.
- **E2 DONE — TS wasm loader** `src/core/autoconfigWasm.ts`: synchronous,
  edge-safe (`initSync` over inlined base64; no `node:*`). Typed camelCase
  `decidePlan`/`classifyColumns` + snake<->camel adapters both ways, plus
  `decidePlanRawJson`/`classifyColumnsRawJson` escape hatches for the parity test.
  Build pipeline: `scripts/build_autoconfig_wasm.mjs` runs wasm-pack, strips the
  async init path from the wasm-bindgen glue (kills the `import.meta.url`/`fetch`
  that would break the CJS build + edge-safety), base64-embeds the wasm, and copies
  the golden vectors into `tests/parity/fixtures/autoconfig/`. Generated artifacts
  live under `src/core/_wasm/` and ARE committed (so `tsc`/`vitest`/`tsup` need no
  rust toolchain). Exposed as the opt-in subpath `goldenmatch/core/autoconfig-wasm`
  (separate tsup entry) so only importers of that subpath pay the ~1.7 MB.
- **E3 parity test DONE** (the cross-surface gate; the reroute itself is NOT done —
  see REMAINING): `tests/parity/autoconfig-core.parity.test.ts` runs the 49 planner
  + 39 classifier golden vectors through the TS wasm path. **92 tests green** — Rust
  + Python + TS now byte-parity on identical JSON. Full TS suite stays green (1532
  pass + 1 expected-fail).
- **E4 DONE — CI wiring:** `rust` job now `cargo check`+clippy's the wasm crate;
  `typescript` job gained a drift guard (gated on a new `autoconfig_wasm` path
  filter) that provisions wasm32+wasm-pack ONLY when the core/wasm/embed-script
  changes, rebuilds the embedded artifacts, diffs the deterministic golden fixtures,
  and re-runs the parity test against the fresh build. (Deliberately does NOT byte-diff
  the .wasm/.js — those vary with the CI rustc/wasm-bindgen version; stale wasm is
  caught behaviorally by the committed parity test when fixtures move.)
- **`BackendName`** widened with `"bucket"` in `executionPlan.ts` (safe superset of
  the core's enum, per the original handoff note).

## STILL TO DO: native-ON parity in CI (couldn't run on the dev box)

The CI `native` job builds the 0.1.6 ext via `scripts/build_native.py` then runs
`tests/test_autoconfig_native_parity.py` (SKIPS without the ext). Confirm it's green
on this PR — it's the end-to-end proof of the Python binding. (Already wired in
`.github/workflows/ci.yml`.)

## REMAINING TASKS

> **E2, the E3 parity test, and E4 are DONE** (see the cloud-session section above).
> What's left is the behavior-changing **E3 reroute** and the **F gate-flip**.

### ~~E2 — TS WASM loader~~ ✅ DONE
`src/core/autoconfigWasm.ts` + `scripts/build_autoconfig_wasm.mjs` + the committed
`src/core/_wasm/` artifacts. Sync `initSync` over inlined base64, camelCase adapters,
opt-in subpath `goldenmatch/core/autoconfig-wasm`. The snake<->camel field mapping is
implemented in the adapters (the original mapping notes below are kept for E3, which
must reconcile the divergent `profiler.ts`/`autoconfigPlannerRules.ts` vocabularies).

### E3 — Reroute TS planner/classifier through the wasm core (THE DEEP ONE — NOT DONE)
The loader exists and is parity-proven; this task swaps the EXISTING TS planner +
classifier internals to call it. Still NOT a simple "delete + call wasm." The cross-
surface gate (`tests/parity/autoconfig-core.parity.test.ts`) is now in place as the
objective target. The TS port's vocabularies DIVERGE from the core, so rerouting
CHANGES TS behavior and WILL break TS tests — must be tsc + vitest verified:
This is NOT a simple "delete + call wasm." The TS port's vocabularies DIVERGE from the
core, so rerouting CHANGES TS behavior and WILL break TS tests — must be tsc + vitest
verified:
- **`ColumnType`** (`profiler.ts`) = 11 values using **`"id"`** (core: `identifier`),
  **`"text"`** (core: `string`), and it **LACKS `address` and `description`**. The core
  emits all 13. Decide: widen the TS `ColumnType` to the core's 13 (and map id<->identifier,
  text<->string), and update every downstream consumer that switches on col_type
  (TS blocking/matchkey selection) + the tests.
- **`BackendName`** (`executionPlan.ts`): `"bucket"` already added (done in E2's PR).
- **Planner rules:** TS `autoconfigPlannerRules.ts` has **6 rules; the core has 8**
  (adds `bucket_suggested` + the `no_rule_matched` fallback + duckdb-conditional). Routing
  through wasm makes TS match Python's 8 — update/replace the TS planner tests accordingly.
- **TS capabilities:** when building the `PlannerInput` caps for the wasm call, set
  `bucket_available=false, ray_available=false, ray_auto_select=false, user_backend=ctx`.
- Keep the public signatures of `applyPlannerRules` / the profiler functions STABLE so
  `autoconfigController.ts` and callers don't change; swap only the internals.
- Then DELETE the now-dead hand-written rule table + classifier heuristics.

### ~~E3 parity test~~ ✅ DONE
`tests/parity/autoconfig-core.parity.test.ts` — 92 tests, Rust+Python+TS byte-parity
on the shared vectors. Fixtures copied into `tests/parity/fixtures/autoconfig/` by the
embed script. This is the objective gate the E3 reroute must keep green.

### ~~E4 — TS build + CI~~ ✅ DONE
`scripts/build_autoconfig_wasm.mjs` (wasm-pack + base64-embed); CI `rust` job checks the
wasm crate; CI `typescript` job has the wasm32+wasm-pack drift guard gated on the new
`autoconfig_wasm` path filter. `tsc --noEmit && vitest run && tsup` all green locally.

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
