# GoldenFlow WASM + Cross-Surface Byte-Parity (Wave 0c) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface GoldenFlow's owned identifier kernels to the edge via a `goldenflow-wasm` crate + TypeScript wiring, and prove one shared corpus is byte-identical across native, WASM/TS, and pure-Python — with `goldenflow-core` as the single oracle.

**Architecture:** A new `goldenflow-wasm` crate (wasm-bindgen) exports the identifier kernels from `goldenflow-core` (string in / string|bool out). The TS package reuses the `goldenmatch-wasm-runtime` substrate for byte-loading + env detection; `enableWasm()` is async and returns `false` (stays pure-TS) on any load failure. TS gets pure-TS identifier fallbacks that reproduce the kernel bytes. The Wave 0b corpus is copied (not symlinked) into the TS fixtures with a CI sync-check; a vitest parity test asserts WASM and pure-TS both equal `expected`.

**Tech Stack:** Rust (wasm-bindgen, `goldenflow-core`), wasm-pack, TypeScript, vitest, pnpm/Turborepo.

**Depends on:** Wave 0b — the identifier kernels in `goldenflow-core` and the `tests/parity/identifiers_corpus.jsonl` oracle must exist. Branch off updated `main` after 0b merges.

**Spec:** `docs/superpowers/specs/2026-07-02-goldenflow-core-cross-surface-wave0-design.md`

**Precedents to read first:**
- `packages/rust/extensions/score-wasm/` — the wasm-bindgen-over-`-core` crate pattern (Cargo.toml, `target_arch = "wasm32"` gating, `src/lib.rs`).
- `packages/typescript/goldenmatch-wasm-runtime/src/index.ts` — the zero-dep byte-loader / env-detect / registry substrate to reuse.
- An existing consumer's `src/core/wasm/{backend,loader,index}.ts` (e.g. goldenanalysis) — the `enableWasm()` + refuses-when-unenabled wiring to mirror.
- `packages/typescript/goldenflow/src/core/transforms/` — where TS transforms live; and the TS CI lane in `.github/workflows/ci.yml`.

**Environment:** wasm builds need the `wasm32-unknown-unknown` target + `wasm-pack` (per the suite build notes; PATH needs the toolchain bin + the npm wasm-pack dir). TS on the Windows box: memory says vitest/builds can OOM locally — prefer the CI TS lane for the full run; locally do `tsc --noEmit` (typecheck) and at most a single targeted vitest file. The `.wasm` artifact is built in CI, NEVER committed (matches the suite wasm gitignore).

---

## File Structure

- **Create** `packages/rust/extensions/goldenflow-wasm/Cargo.toml` — own standalone `[workspace]` block (self-isolates like `score-wasm` — do NOT add it to the extensions `exclude` list), wasm-bindgen under a `wasm32` target-cfg dep, `goldenflow-core` path dep, `crate-type = ["cdylib", "rlib"]`.
- **Create** `packages/rust/extensions/goldenflow-wasm/src/lib.rs` — `#[wasm_bindgen]` exports for the identifier kernels (string in / string|bool out).
- **Modify** `packages/rust/extensions/Cargo.toml` — add `goldenflow-wasm` to `exclude`.
- **Create** `packages/typescript/goldenflow/src/core/wasm/{backend.ts,loader.ts,index.ts}` — `enableWasm()`, registry, refuses-when-unenabled; reuse `goldenmatch-wasm-runtime`.
- **Create/Modify** `packages/typescript/goldenflow/src/core/transforms/identifiers.ts` — pure-TS identifier fallbacks + WASM dispatch.
- **Create** `packages/typescript/goldenflow/src/core/wasm/artifacts/.gitignore` — ignore the built `.wasm`/JS glue (mirror the suite pattern).
- **Create** `packages/typescript/goldenflow/tests/parity/identifiers_corpus.jsonl` — COPY of the Python corpus (CI sync-checked).
- **Create** `packages/typescript/goldenflow/tests/parity/identifiers.parity.test.ts` — vitest: WASM and pure-TS each == `expected`.
- **Modify** `.github/workflows/ci.yml` — build the wasm bundle in the goldenflow TS lane, run the parity test, and add a corpus sync-check (Python fixture == TS fixture).

---

## Task 1: `goldenflow-wasm` crate

**Files:** `goldenflow-wasm/Cargo.toml`, `goldenflow-wasm/src/lib.rs`, `packages/rust/extensions/Cargo.toml`.

- [ ] **Step 1:** Read `score-wasm/Cargo.toml` and mirror it EXACTLY (shape, not just the pin). Create `goldenflow-wasm/Cargo.toml`: its own empty `[workspace]` block (this self-isolates from the bridge workspace — score-wasm does the same and is NOT in the extensions `exclude` list, so do NOT edit `packages/rust/extensions/Cargo.toml`), `[package] name = "goldenflow-wasm"`, `[lib] crate-type = ["cdylib", "rlib"]` (rlib kept so host-side `cargo test` can link the crate, matching score-wasm), and `wasm-bindgen` under `[target.'cfg(target_arch = "wasm32")'.dependencies]` (NOT a bare top-level dep — copy score-wasm's target-cfg placement) plus `goldenflow-core = { path = "../goldenflow-core" }`.

- [ ] **Step 2:** Create `src/lib.rs` — thin `#[wasm_bindgen]` exports over `goldenflow_core::identifiers`, one per transform, string in / bool|string-or-null out:
```rust
use wasm_bindgen::prelude::*;
use goldenflow_core::identifiers::{luhn, iban, isbn, ean, vat};

#[wasm_bindgen] pub fn cc_validate(s: &str) -> bool { luhn::cc_validate(s) }
#[wasm_bindgen] pub fn cc_format(s: &str) -> Option<String> { luhn::cc_format(s) }
#[wasm_bindgen] pub fn cc_mask(s: &str) -> Option<String> { luhn::cc_mask(s) }
#[wasm_bindgen] pub fn iban_validate(s: &str) -> bool { iban::iban_validate(s) }
#[wasm_bindgen] pub fn iban_format(s: &str) -> Option<String> { iban::iban_format(s) }
#[wasm_bindgen] pub fn isbn_validate(s: &str) -> bool { isbn::isbn_validate(s) }
#[wasm_bindgen] pub fn isbn_normalize(s: &str) -> Option<String> { isbn::isbn_normalize(s) }
#[wasm_bindgen] pub fn ean_validate(s: &str) -> bool { ean::ean_validate(s) }
#[wasm_bindgen] pub fn vat_validate(s: &str) -> bool { vat::vat_validate(s) }
#[wasm_bindgen] pub fn vat_format(s: &str) -> Option<String> { vat::vat_format(s) }
```
(`Option<String>` maps to `string | undefined` across the wasm-bindgen boundary; the TS layer normalizes `undefined` → `null` to match the corpus.)

- [ ] **Step 3:** Build to confirm it compiles to wasm: `cargo build --target wasm32-unknown-unknown --release` from the crate dir (and `wasm-pack build --target web` if the toolchain is available locally; otherwise defer the pack to CI). `cargo-clippy clippy --target wasm32-unknown-unknown -- -D warnings`, `cargo fmt --check`.

- [ ] **Step 4: Commit** `feat(goldenflow-wasm): wasm-bindgen surface over goldenflow-core identifiers`.

---

## Task 2: TS WASM wiring (reuse the runtime substrate)

**Files:** `packages/typescript/goldenflow/src/core/wasm/{backend.ts,loader.ts,index.ts}`, `artifacts/.gitignore`.

- [ ] **Step 1:** Read an existing consumer's `src/core/wasm/*` (e.g. goldenanalysis) and `goldenmatch-wasm-runtime/src/index.ts`. Mirror the wiring: `loader.ts` loads the built `.wasm` bytes via the runtime substrate (env-detect: Node vs browser/Worker); `backend.ts` wraps the wasm exports as typed functions; `index.ts` exposes `enableWasm(): Promise<boolean>` (async, returns `false` on any load failure → stays pure-TS) plus a registry the transforms consult.

- [ ] **Step 2:** Add `artifacts/.gitignore` ignoring the built `.wasm` + JS glue (mirror the suite: the artifact is CI-built, never committed).

- [ ] **Step 3:** `pnpm --filter goldenflow typecheck` (`tsc --noEmit`, 0 errors). Do NOT run the full vitest suite locally (OOM risk) — CI covers it.

- [ ] **Step 4: Commit** `feat(goldenflow-js): wasm loader/backend wiring (reuses goldenmatch-wasm-runtime)`.

---

## Task 3: Pure-TS identifier fallbacks + dispatch

**Files:** `packages/typescript/goldenflow/src/core/transforms/identifiers.ts`.

- [ ] **Step 1:** Implement pure-TS `ccValidate/ccFormat/ccMask/ibanValidate/ibanFormat/isbnValidate/isbnNormalize/eanValidate/vatValidate/vatFormat` that reproduce the Rust kernels byte-for-byte (same strip, same checksums, same grouping/mask, same VAT prefix table + checksum coverage). These are the default + fallback (pure-TS is always the default; WASM is opt-in via `enableWasm()`).

- [ ] **Step 2:** Wire dispatch: each transform checks the wasm registry (from Task 2) — if WASM is enabled and the export is present, call it; else pure-TS. Both must produce identical output (Task 4 proves it).

- [ ] **Step 3:** `pnpm --filter goldenflow typecheck`. Commit `feat(goldenflow-js): pure-TS identifier transforms + wasm dispatch`.

---

## Task 4: Cross-surface byte-parity harness (TS side)

**Files:** `packages/typescript/goldenflow/tests/parity/identifiers_corpus.jsonl`, `tests/parity/identifiers.parity.test.ts`.

- [ ] **Step 1:** COPY `packages/python/goldenflow/tests/parity/identifiers_corpus.jsonl` to `packages/typescript/goldenflow/tests/parity/identifiers_corpus.jsonl` (copy, not symlink — Windows). This is the SAME oracle corpus the Python + native surfaces assert against.

- [ ] **Step 2:** Write `identifiers.parity.test.ts` (vitest): load the corpus; for each row, run BOTH the pure-TS transform and (after `await enableWasm()`) the WASM path, asserting each equals `expected` byte-for-byte (normalize wasm `undefined` → `null`; `"true"`/`"false"` → boolean compare). If `enableWasm()` returns false in the test env (no built artifact), skip the WASM leg with a clear message but still run pure-TS (CI builds the artifact so the WASM leg runs there).

- [ ] **Step 3:** Run just this file locally if feasible (`pnpm --filter goldenflow vitest run tests/parity/identifiers.parity.test.ts`); otherwise rely on CI. Commit `test(goldenflow-js): cross-surface identifier byte-parity (wasm + pure-TS vs oracle)`.

---

## Task 5: CI — build wasm, run parity, corpus sync-check

**Files:** `.github/workflows/ci.yml` (the goldenflow TS lane).

- [ ] **Step 1:** There is NO existing goldenflow TS+wasm lane. Create a **new job `wasm_flow`** modeled line-for-line on the existing **`wasm_score`** job in `ci.yml` (read `wasm_score` first): its own `changes` path filter on `packages/rust/extensions/goldenflow-wasm/**` (+ the goldenflow TS transform paths), rust-toolchain with the `wasm32-unknown-unknown` target, the wasm build (mirror score's `build_wasm.sh` / `wasm-pack build packages/rust/extensions/goldenflow-wasm --target web --out-dir <the TS artifacts dir>`), then the vitest parity run, and wire the job into `ci-required.needs`. Do not try to extend a non-existent lane.
- [ ] **Step 2:** Add a **corpus sync-check**: a step asserting `packages/python/goldenflow/tests/parity/identifiers_corpus.jsonl` and `packages/typescript/goldenflow/tests/parity/identifiers_corpus.jsonl` are byte-identical (`diff` / `cmp`), failing if they drift. Wire the goldenflow TS lane / new wasm build into `ci-required` if not already, and add `packages/rust/extensions/goldenflow-wasm/**` to the relevant path filter.
- [ ] **Step 3: Commit** `ci(goldenflow): build wasm + cross-surface parity + corpus sync-check`.

---

## Task 6: Docs sweep (end of Wave 0) + PR

**This is the deferred end-of-Wave-0 docs sweep** (0a and 0b deferred it here). Use the `rollout-docs-sweep` skill.

- [ ] **Step 1:** Sweep every doc surface for the whole Wave 0 (0a+0b+0c): goldenflow `CLAUDE.md` (new `goldenflow-core`/`native-flow`/`goldenflow-wasm` crate split, reference-mode loader, the 10 identifier transforms, the byte-parity harness), the transform-count source-of-truth line, goldenflow README + TS README (new transforms, `enableWasm()`), the tuning/runtime-config doc (`GOLDENFLOW_NATIVE` semantics under reference-mode), `llms.txt`/discovery surfaces, CHANGELOG, and the context-network ADR (a new ADR: "goldenflow reference-mode + owned identifier kernels + cross-surface"). Relabel the Python/TS pure paths as non-authoritative fallbacks where appropriate.
- [ ] **Step 2:** Version bumps: `goldenflow` (Python) minor (already bumped in 0b if 0b shipped separately — confirm), `goldenflow` (npm) minor for the new TS transforms + `enableWasm()`, `goldenflow-native` wheel lockstep if new symbols shipped. Update CHANGELOG entries.
- [ ] **Step 3: Commit** `docs(goldenflow): Wave 0 rollout docs sweep (core split, identifiers, cross-surface)`.
- [ ] **Step 4:** Push `feat/goldenflow-wasm-0c` (off updated main after 0b merged). Open PR `feat(goldenflow): Wave 0c — WASM/TS surface + cross-surface byte-parity`. Body: goldenflow-wasm crate, TS transforms + `enableWasm()`, one oracle corpus proven byte-identical across native/WASM/Python, docs sweep. Arm `--auto --squash` once the TS + wasm + parity lanes are green.

---

## Notes / guardrails

- **Pure-TS stays the default and permanent fallback** (edge-safety: browsers/Workers, no `node:*`, zero-dep). `enableWasm()` is opt-in and async; any load failure → `false` → pure-TS. Never make WASM the default.
- **One corpus, three surfaces, one oracle.** The Python fixture (generated from `goldenflow-core` in 0b) is authoritative; the TS fixture is a byte-copy, CI-enforced. If any surface disagrees, the fallback (Python/TS) is wrong and must be fixed to the Rust bytes — not the other way around.
- **The `.wasm` is CI-built, never committed.** Default TS users load zero wasm bytes.
- **Don't run the full TS vitest suite locally** (OOM). Typecheck locally; let CI run vitest + the wasm build.
- **SQL surface stays out of scope** (deferred beyond Wave 0). This closes Wave 0 at native + WASM/TS.
