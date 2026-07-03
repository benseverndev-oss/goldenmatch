# GoldenProfile (Virtual Fingerprint) on WASM + TS — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the GoldenProfile **Virtual Fingerprint** engine (cross-document entity resolution) to the TS/JS surface by wiring the **already-built** `goldenprofile-wasm` crate into a new, standalone, edge-safe `goldenprofile` npm package, with full parity to the Python `goldenprofile_native.resolve_json` boundary (byte-identical clusters; 4dp on edge scores).

**Why this target:** It is the smallest beachhead into `goldengraph`'s TS world. The whole kernel chain already exists — `goldenprofile-core` (kernel) → `goldenprofile-native` (Python) → `goldenprofile-cabi` (C ABI) → **`goldenprofile-wasm` (built, exports `resolve_json`)**. The **only missing leg is the TS wiring**. `goldengraph-wasm` (22 fns) is the larger follow-on; landing the single-function `goldenprofile` resolver first establishes the goldengraph-family TS+wasm precedent at minimum size.

**Architecture:** One Rust kernel (`goldenprofile-core`), a second non-Python binding (`goldenprofile-wasm`, a `wasm-bindgen` `cdylib`) that already exists alongside the Python pyo3 shim and the C ABI. The engine is byte-identical across Python / WASM / C **by construction** — all three wrap the same `resolve_json` boundary, no re-implementation. TS rides that boundary via a lean, edge-safe registry backend (default = unregistered → an actionable throw, mirroring Python's `_engine()` raise) that a heavy opt-in `goldenprofile/wasm` subpath registers. This is the exact TS analog of `pip install goldenprofile-native` / the goldenmatch `core/suggest-wasm` opt-in subpath.

**Precedent track:** This follows the **healer/suggest** model (`2026-06-26-healer-wasm-ts-parity`), **not** the score/analysis model. There is no pre-existing pure-TS resolver, so the "pure-TS default + measured-win-vs-pure-TS" gate of ADR 0014 does not apply here. The binding gate is **parity + a functional/scale bench** (feature enablement), exactly as the healer shipped. Edge-safety is preserved the same way: the base package is pure-TS types + a zero-byte registry; only the opt-in subpath carries wasm bytes.

**Tech Stack:** Rust (`goldenprofile-core`, `goldenprofile-wasm`, `wasm-bindgen`, `wasm-pack`, `wasm32-unknown-unknown`), TypeScript (new `goldenprofile` pkg: vitest, tsup, esbuild), Python (parity fixtures only).

**Contract (the JSON boundary, verified against `goldenprofile-core/src/{model,resolve}.rs`):**
- **Request:** `{"profiles":[{"kind":"node"|"edge","name":string,"category":string,"anchor":string,"attribute":string}, ...]}`
- **Response (`Resolution`):** `{"clusters": number[][], "edges": [{"a":usize,"b":usize,"score":PairScore}, ...]}` — `clusters` partitions **every** profile index (singletons included); `edges` is the scored-merge audit trail.
- **Single entry:** `resolve_json(request: &str) -> Result<String, JsValue>` (wasm) / `-> PyResult<String>` (py). Self-contained: no embeddings/ONNX on the `{"profiles": ...}` path.

---

## Reference files to mirror (READ THESE FIRST)

The repo has a complete `-core → -wasm → TS` precedent for the **healer (suggest)** that is a near-exact structural match (single JSON-boundary fn, opt-in, no pure-TS fallback). Mirror it; do not invent a new pattern.

| Concern | Mirror this file exactly |
|---|---|
| wasm-bindgen wrapper crate (already exists — verify only) | `packages/rust/extensions/goldenprofile-wasm/{Cargo.toml,src/lib.rs}` |
| build script (wasm-pack → committed glue + base64 bytes + fixtures) | `packages/typescript/goldenmatch/scripts/build_suggest_wasm.mjs` |
| lean registry backend (edge-safe, `import type` only, registry singleton) | `packages/typescript/goldenmatch/src/core/suggestWasmBackend.ts` |
| heavy opt-in module + `initSync` + `enable*()` | `packages/typescript/goldenmatch/src/core/suggestWasm.ts` |
| committed wasm outputs (bindings + base64 bytes) | `packages/typescript/goldenmatch/src/core/_wasm/suggestWasm*.{js,d.ts,ts}` |
| `package.json` subpath export for the opt-in heavy path | `packages/typescript/goldenmatch/package.json` → `"./core/suggest-wasm"` block |
| wasm cross-parity test (TS side) | `packages/typescript/goldenmatch/tests/parity/suggest-wasm.parity.test.ts` |
| wasm cross-parity test (Python side) | `packages/python/goldenmatch/tests/test_suggest_wasm_crossparity.py` |
| Python behavior being matched (the surface + the `_engine()` raise) | `packages/python/goldengraph/goldengraph/profile.py` (`resolve_profiles`, `_engine`) |
| shared zero-dep wasm runtime (only if a loader helper is needed) | `packages/typescript/goldenmatch-wasm-runtime/src/index.ts` |
| a minimal standalone TS package to clone for scaffold | `packages/typescript/goldenpipe/` (small, standalone, edge-safe layout) |
| governing policy | `context-network/decisions/0014-opt-in-wasm-acceleration.md` + `context-network/architecture/wasm-acceleration.md` |

**Toolchain / environment constraints (from repo CLAUDE.md + memory):**
- **TS build/test OOMs Ben's Windows box — run `tsc`/`vitest`/`tsup` in CI, not locally** (`feedback_box_memory_oom_ts`). Targeted single-file `vitest` is sometimes OK; the full suite is not. Committed `_wasm/` artifacts mean TS tooling needs **no** Rust toolchain.
- The wasm build needs `wasm-pack` + the `wasm32-unknown-unknown` target. Run `build_goldenprofile_wasm.mjs` where those exist; **commit its outputs**.
- Rust: prefer `cargo check`/`cargo test -p goldenprofile-core -p goldenprofile-wasm` (both are small, standalone-workspace crates — they will not OOM like the heavy ext crates).
- TS worktree install on exFAT D: has known friction — see `reference_ts_worktree_install_exfat`.
- Do the work in a worktree (`superpowers:using-git-worktrees`); branch off `main`, not `feat/1299-...`.

---

## Phase A — Kernel / wasm crate (verify-only; the binding already exists)

- [ ] **A1.** `cargo test -p goldenprofile-core -p goldenprofile-wasm` (host) — confirm the existing `resolve_json_impl` host-parity test passes (`impl_resolves_and_matches_core`). No code change expected.
- [ ] **A2.** Confirm the crate builds to wasm32: `wasm-pack build packages/rust/extensions/goldenprofile-wasm --target web` (or run via the Phase-B script). Verify the `cdylib` emits `resolve_json` and pulls **no** `ort`/onnx/arrow (Cargo.toml already shows only `goldenprofile-core` + `wasm-bindgen` — assert this stays true; if a transitive `arrow`/`ort` appears, feature-gate it off for wasm32 exactly as `autoconfig-core` gates `arrow = [...] optional=true`).
- [ ] **A3.** If `resolve_json` ever needs a config arg from the caller, confirm the wasm signature still matches the Python `resolve_json(request)` single-string boundary. (Today it is single-arg — keep it that way; pack any options INTO the request JSON, never as a second wasm param.)

**Acceptance:** host parity test green; wasm32 build emits `resolve_json`; zero native (ort/onnx) deps in the wasm artifact.

---

## Phase B — Build pipeline (wasm-pack → committed artifacts + fixtures)

- [ ] **B1.** Add `packages/typescript/goldenprofile/scripts/build_goldenprofile_wasm.mjs`, cloned from `build_suggest_wasm.mjs`: run `wasm-pack build --target web` on `goldenprofile-wasm`, then emit committed:
  - `src/core/_wasm/goldenprofileWasmBindings.js` + `.d.ts` (the wasm-bindgen glue)
  - `src/core/_wasm/goldenprofileWasmBytes.ts` (base64-encoded `.wasm`, so the bundle is self-contained and TS tooling needs no Rust)
- [ ] **B2.** In the same script, regenerate the **shared parity fixtures** by invoking the host boundary (call `goldenprofile_native.resolve_json` via Python, or the cabi, over a curated request set) and writing `tests/parity/fixtures/goldenprofile_resolutions.json` (request → expected `Resolution`). One source of truth, consumed by BOTH the TS and Python parity tests.
- [ ] **B3.** Curate the fixture request set to cover: a clean 2-doc merge (the crate's own `Acme Inc`/`Acme` case → 1 cluster), an anti-shatter near-miss that must STAY split, a singleton, mixed `node`+`edge` kinds (must never cross-merge), `UNKNOWN` anchors, non-BMP / unicode names (the codepoint-iteration footgun from #879), and a large-N set (≥10k profiles) to exercise the batch boundary + catch large-array footguns (the analysis `Math.min(...vals)` stack-overflow lesson from ADR 0014).
- [ ] **B4.** Commit all generated artifacts. Document in the script header that it is the ONLY way to regenerate them and must be re-run whenever `goldenprofile-core` changes (the wheel/symbol-skew lesson — `project_688_stale_native_wheel`).

**Acceptance:** running the script produces committed `_wasm/*` + a `goldenprofile_resolutions.json` fixture; re-running is idempotent (byte-stable).

---

## Phase C — TS package scaffold + opt-in backend

- [ ] **C1.** Scaffold `packages/typescript/goldenprofile/` cloning `goldenpipe`'s layout: `package.json` (name `goldenprofile`, edge-safe, zero runtime deps in the base entry), `tsconfig.json`, `tsup.config.ts`, `vitest.config.ts`, `README.md`, `LICENSE`. Wire it into the pnpm workspace + Turbo.
- [ ] **C2.** `src/core/goldenprofileWasmBackend.ts` — edge-safe registry (mirror `suggestWasmBackend.ts`): a `GoldenprofileWasmBackend` interface (`resolveJson(request: string): string`), a module-singleton with `setGoldenprofileWasmBackend` / `getGoldenprofileWasmBackend` / `disableGoldenprofileWasm` / `isGoldenprofileWasmEnabled`. **No** `node:` imports, **zero** wasm bytes pulled here.
- [ ] **C3.** `src/core/goldenprofileWasm.ts` (the heavy opt-in module) — mirror `suggestWasm.ts`: import the committed `_wasm/` glue + bytes, `initSync`, and an `enableGoldenprofileWasm()` that constructs the backend and calls `setGoldenprofileWasmBackend`. This is the ONLY module that loads wasm bytes.
- [ ] **C4.** `package.json` `exports`: base `"."` (types + registry + `resolveProfiles`) and the heavy `"./wasm"` subpath → `dist/core/goldenprofileWasm.*`. Mirror the goldenmatch `"./core/suggest-wasm"` block.
- [ ] **C5.** `src/index.ts` — typed public surface:
  - Types: `Profile`, `ResolveRequest`, `Resolution`, `ResolvedEdge`, `PairScore` (hand-authored to match the Rust serde shape; these are the TS source of truth for the boundary).
  - `resolveProfiles(request: ResolveRequest): Resolution` — JSON-encodes, calls `getGoldenprofileWasmBackend()`, JSON-decodes. **When unregistered, throw an actionable error** ("GoldenProfile resolution requires the wasm backend. `import { enableGoldenprofileWasm } from 'goldenprofile/wasm'` and call it first.") — the exact analog of Python's `_engine()` raise. Do NOT return a fake/empty Resolution (unlike the healer's additive `[]`, an empty resolution is silently wrong).

**Acceptance:** `pnpm --filter goldenprofile build` (in CI) produces base + `./wasm` entry points; importing the base entry pulls zero wasm bytes; calling `resolveProfiles` without enabling throws the actionable error.

---

## Phase D — Parity (the binding gate, part 1)

- [ ] **D1.** `tests/parity/goldenprofile-wasm.parity.test.ts` — load `goldenprofile_resolutions.json`, `enableGoldenprofileWasm()`, and assert for every fixture: `clusters` are **byte-identical** (same partition, same canonical ordering) and every `edge.score` field matches the expected to **4 decimals** (incl. the non-BMP and large-N cases). Mirror `suggest-wasm.parity.test.ts`.
- [ ] **D2.** `packages/python/goldengraph/tests/test_goldenprofile_wasm_crossparity.py` — assert `goldenprofile_native.resolve_json` over the SAME fixture set equals the fixture's expected `Resolution`, closing the Python↔WASM loop through the shared file (mirror `test_suggest_wasm_crossparity.py`). `importorskip('goldenprofile_native')`.
- [ ] **D3.** Add a TS unit test for the unregistered-throw path and `disable/enable` isolation (mirror `tests/unit/suggestWasmBackend.test.ts`).

**Acceptance:** Python↔WASM byte-identical clusters + 4dp edge scores across all fixtures; the unregistered path throws; xdist-safe (register inside each test — `pytest -n auto` worker isolation).

---

## Phase E — Bench (the binding gate, part 2: functional/scale, not vs-pure-TS)

- [ ] **E1.** `scripts/bench_goldenprofile_wasm.mjs` — measure wall on the ≥10k-profile fixture: enable wasm, resolve, report wall + cluster count. This is a **dist-validation / scale** bench (the healer track), NOT a speedup-vs-pure-TS gate — there is no pure-TS resolver to beat. Its job: prove the boundary is crossed once per call (batch, not per-pair) and the artifact survives the large-array case (the ADR-0014 `Math.min` footgun).
- [ ] **E2.** Record the honest posture in the PR + ADR: goldenprofile is an **enablement** fold (feature did not exist on TS before), so the gate is **parity + runs-at-scale**, explicitly not the acceleration gate. State it plainly (`feedback_verify_perf_not_just_ship`: don't imply a speedup that wasn't measured).

**Acceptance:** bench runs green on ≥10k profiles, single boundary crossing confirmed, no large-array crash; posture documented as enablement.

---

## Phase F — CI + docs sweep

- [ ] **F1.** `.github/workflows/ci.yml` — add a `goldenprofile` entry to the `dorny/paths-filter` `changes` job and gate a TS build/parity lane on it (mirror the existing `suggest`/`autoconfig` wasm lanes). Add the Python cross-parity test to the goldengraph pytest `--ignore`/include set. Remember: editing `ci.yml` itself force-runs every job.
- [ ] **F2.** A wasm-build CI step (where `wasm-pack` + `wasm32` exist) that rebuilds the artifact and `git diff --exit-code`s the committed `_wasm/*` — so a stale-artifact drift fails CI (the symbol-skew guard).
- [ ] **F3.** Run the **`rollout-docs-sweep`** skill (`feedback_rollout_docs_sweep`). At minimum: a new ADR (`00NN-goldenprofile-wasm-ts.md`, modeled on 0014/0027) recording the standalone-package decision + enablement posture; `context-network` nav + updates log; `docs-site` tuning/opt-in entry (the `goldenprofile/wasm` subpath, analogous to the goldenmatch native opt-in); the new package README; CHANGELOG; and any suite discovery surface (llms.txt / registry) that enumerates packages.
- [ ] **F4.** npm publish wiring: a `publish-goldenprofile-js.yml` (or fold into the existing TS publish matrix) on a `goldenprofile-js-v*` tag — mirror the goldenmatch-js publish flow. **Defer actual publish** until Ben approves the new public package name.

**Acceptance:** CI gates the new lane; the stale-artifact guard works; every doc surface in the sweep inventory reflects the new TS surface; publish wiring exists but is not fired.

---

## Open decisions / checkpoints (raise BEFORE Phase C)

1. **Package home — standalone `goldenprofile` vs. a future `goldengraph` TS package.** This plan assumes a **standalone** `packages/typescript/goldenprofile` (rationale: the crate family is a deliberate "standalone workspace"; goldenprofile is a self-contained resolver; goldengraph TS is the deferred SP3 and shouldn't block this). If Ben would rather wait and land `goldenprofile` *inside* a new `goldengraph` TS package alongside `goldengraph-wasm`, that's a bigger first step — flag and confirm. **Recommended: standalone now, `goldengraph` TS as the follow-on that depends on it.**
2. **Public npm name.** `goldenprofile` vs a scoped `@goldensuite/goldenprofile`. Confirm before F4.
3. **Relationship to `goldengraph-wasm` (22 fns).** Out of scope here, but this package is the template it will mirror. Note in the ADR that `goldengraph-wasm` is the next fold after this.

## Definition of done

- `goldenprofile-wasm` is consumed by a working, edge-safe TS package; base import pulls zero wasm bytes; the `./wasm` opt-in enables resolution.
- Python↔WASM parity proven byte-identical (clusters) + 4dp (edge scores) over a shared fixture set incl. non-BMP and ≥10k-N.
- CI gates the lane + guards artifact staleness; docs swept; publish wired-but-unfired.
- Posture documented honestly as **enablement** (parity + scale), not acceleration.
