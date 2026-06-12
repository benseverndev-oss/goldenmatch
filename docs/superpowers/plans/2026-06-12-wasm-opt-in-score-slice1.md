# Opt-in WASM acceleration — Slice 1 (score-core → goldenmatch TS) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship an opt-in WASM backend for the goldenmatch TS scorer that wraps the existing pyo3-free `score-core` crate, keeping pure-TS the zero-dependency default and fallback.

**Architecture:** A new `score-wasm` wasm-bindgen crate (own Cargo workspace) path-depends on `goldenmatch-score-core` — the same crate the Python `native` wheel and DataFusion UDFs wrap, so parity is structural at the Rust level. On the TS side, an async `enableWasm()` lazily loads + instantiates the `.wasm`, registers a `ScorerBackend`, and the existing **sync** `scoreMatrix` swaps to it **per covered scorer** (jaro_winkler / levenshtein / exact for slice 1) at the **matrix (NxN block) boundary**, never per pair. A skip-guarded parity test (WASM ≈ pure-TS ≈ Python goldens, 4dp) and a wall-clock benchmark gate it in a new CI lane.

**Tech Stack:** Rust + wasm-bindgen + `wasm32-unknown-unknown`; TypeScript (tsup/vitest, strict mode); GitHub Actions (`dorny/paths-filter`).

**Spec:** `docs/superpowers/specs/2026-06-12-opt-in-wasm-rust-acceleration-design.md`

---

## Pre-flight (read once, do not skip)

- **Branch:** This work is NOT related to the current `feat/857-*` branch. Create a fresh branch off `main`: `git checkout main && git pull && git checkout -b feat/wasm-opt-in-score`. (Per `feedback_branch_merge_sop`: feature branch → squash-merge PR → merge-on-green is pre-authorized.)
- **Rust bash preamble** (prepend to EVERY cargo/rustup command, per `packages/rust/extensions/CLAUDE.md`):
  ```bash
  export PATH="/c/Users/bsevern/.cargo/bin:$PATH" && export RUSTUP_HOME="C:/Users/bsevern/.rustup" && export CARGO_HOME="C:/Users/bsevern/.cargo"
  ```
- **Memory constraints** (`feedback_box_memory_oom_ts`, `feedback_avoid_full_suite_oom`): the box OOMs on the full TS suite and heavy builds. Run **single-file** vitest locally (`npx vitest run tests/<file>.test.ts`), never the whole suite. The full suite + the wasm parity test are authoritative **in CI**.
- **WASM-covered scorers for slice 1: `jaro_winkler`, `levenshtein`, `exact` only.** `token_sort` is deferred — `score-core`'s token_sort does NOT lowercase/strip but the TS/Python path does, and how the Python native wheel normalizes needs investigation (spec §Architecture). Every other scorer always routes to pure-TS even when WASM is enabled. Do not widen this set in slice 1.
- **`.wasm` is NOT committed to git.** It is built in CI and bundled into the npm tarball at publish. Local opt-in testing requires running the build script first; without it, the parity test skips and pure-TS is used.

---

## File Structure

**Rust (new crate — own workspace, like `score-core`/`native`):**
- Create `packages/rust/extensions/score-wasm/Cargo.toml`
- Create `packages/rust/extensions/score-wasm/src/lib.rs` — host-testable `*_impl` fns + thin `#[wasm_bindgen]` wrappers
- Create `packages/rust/extensions/score-wasm/.gitignore` — `/target`, `/pkg`
- Create `packages/rust/extensions/score-wasm/build_wasm.sh` — build + wasm-bindgen + copy artifact into the TS package

**TypeScript (goldenmatch, all edge-safe under `src/core/`):**
- Create `packages/typescript/goldenmatch/src/core/wasm/backend.ts` — `ScorerBackend` interface + module singleton
- Create `packages/typescript/goldenmatch/src/core/wasm/loader.ts` — universal byte loader + instantiation
- Create `packages/typescript/goldenmatch/src/core/wasm/index.ts` — `enableWasm` / `disableWasm`
- Create `packages/typescript/goldenmatch/src/core/wasm/artifacts/.gitignore` — ignore the built `.wasm` + glue (keep dir tracked)
- Modify `packages/typescript/goldenmatch/src/core/scorer.ts` — make exported `scoreMatrix` backend-aware
- Modify `packages/typescript/goldenmatch/src/core/index.ts` — export `enableWasm`/`disableWasm`/`ScorerBackend`
- Modify `packages/typescript/goldenmatch/tsup.config.ts` — emit the wasm artifact into `dist`

**Tests / bench:**
- Create `packages/typescript/goldenmatch/tests/unit/wasm-backend.test.ts` — backend singleton + stub-backend swap (no artifact needed)
- Create `packages/typescript/goldenmatch/tests/unit/wasm-fallback.test.ts` — `enableWasm()` returns false gracefully w/o artifact
- Create `packages/typescript/goldenmatch/tests/parity/wasm-scorer.test.ts` — CI-gated parity (skips w/o artifact)
- Create `packages/typescript/goldenmatch/scripts/bench_wasm_scorer.mjs` — 5-run median wall

**CI / docs:**
- Modify `.github/workflows/ci.yml` — new `changes` filter entry + new job
- Modify `packages/typescript/goldenmatch/CLAUDE.md`, `README.md`, `CHANGELOG.md`

---

## Task 1: `score-wasm` crate — host-testable kernel + wasm shims

**Files:**
- Create: `packages/rust/extensions/score-wasm/Cargo.toml`
- Create: `packages/rust/extensions/score-wasm/src/lib.rs`
- Create: `packages/rust/extensions/score-wasm/.gitignore`

- [ ] **Step 1: Write the crate manifest**

`packages/rust/extensions/score-wasm/Cargo.toml`:
```toml
# Standalone workspace so this wasm-bindgen wrapper can path-depend on the
# pyo3-free `score-core` WITHOUT either crate's workspace claiming it — same
# isolation rationale as `score-core`/`native`. NO rust-toolchain.toml: inherits
# the caller's toolchain. This crate is the TS analogue of `native` (pyo3): it
# wraps the SAME `score-core`, so scoring is byte-identical by construction.
[workspace]

[package]
name = "goldenmatch-score-wasm"
version = "0.1.0"
edition = "2021"
license = "MIT"
authors = ["Ben Severn <benzsevern@gmail.com>"]
description = "wasm-bindgen wrapper over goldenmatch-score-core for the goldenmatch TS opt-in WASM backend"

[lib]
name = "goldenmatch_score_wasm"
crate-type = ["cdylib", "rlib"]  # cdylib for wasm; rlib so host unit tests link

[dependencies]
goldenmatch-score-core = { path = "../score-core" }

[target.'cfg(target_arch = "wasm32")'.dependencies]
wasm-bindgen = "0.2"
```

- [ ] **Step 2: Write the failing host test + the impl**

`packages/rust/extensions/score-wasm/src/lib.rs`:
```rust
//! wasm-bindgen wrapper over `goldenmatch-score-core`. The TS analogue of the
//! `native` pyo3 crate: thin shims delegating to `score-core` so the scorers
//! are byte-identical across Python, the FFI UDFs, and TS WASM.
//!
//! Slice-1 covered scorer ids (must match the TS backend): 0=jaro_winkler,
//! 1=levenshtein, 3=exact. id=2 (token_sort) is deliberately NOT wired on the
//! TS side in slice 1 (normalization parity unresolved — see the design spec).
//!
//! Boundary design: the batch `score_matrix` entry crosses the JS↔WASM boundary
//! ONCE per NxN block (values arrive as one separator-joined string), never per
//! pair — per the perf-audit lesson that boundary cost dwarfs a single scorer.

use goldenmatch_score_core::score_one;

/// Full row-major NxN similarity matrix for `values` under `scorer_id`.
/// Diagonal = 0.0 and the matrix is symmetric, matching the pure-TS
/// `scoreMatrix` (which fills the upper triangle, mirrors it, and leaves the
/// diagonal 0). NULL handling is done JS-side (this sees only strings).
pub fn score_matrix_impl(values: &[&str], scorer_id: u8) -> Vec<f64> {
    let n = values.len();
    let mut out = vec![0.0_f64; n * n];
    for i in 0..n {
        for j in (i + 1)..n {
            let s = score_one(scorer_id, values[i], values[j]);
            out[i * n + j] = s;
            out[j * n + i] = s;
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn matrix_is_symmetric_zero_diagonal() {
        // jaro_winkler id=0. "abc"/"abc" on the diagonal stays 0 (diagonal is
        // never scored); off-diagonal is the real score and mirrored.
        let vals = ["abc", "abd", "xyz"];
        let m = score_matrix_impl(&vals, 0);
        assert_eq!(m.len(), 9);
        assert_eq!(m[0], 0.0); // diagonal
        assert_eq!(m[1], m[3]); // symmetric (0,1)==(1,0)
        assert!(m[1] > 0.0 && m[1] < 1.0); // abc~abd is a partial match
    }

    #[test]
    fn exact_id3_is_one_or_zero() {
        let vals = ["a", "a", "b"];
        let m = score_matrix_impl(&vals, 3);
        assert_eq!(m[1], 1.0); // (0,1) a==a
        assert_eq!(m[2], 0.0); // (0,2) a!=b
    }
}
```

- [ ] **Step 3: Run the host tests — verify they pass** (logic lives in `score-core`, already correct)

Run (with preamble):
```bash
cd packages/rust/extensions/score-wasm && cargo test
```
Expected: `test result: ok. 2 passed`.

- [ ] **Step 4: Add the `#[wasm_bindgen]` boundary wrappers**

Append to `src/lib.rs` (guarded to the wasm target so host tests + clippy don't need wasm-bindgen):
```rust
#[cfg(target_arch = "wasm32")]
mod wasm {
    use super::score_matrix_impl;
    use wasm_bindgen::prelude::*;

    /// JS entry: `values` is one string with fields joined by `sep` (a 1-char
    /// separator the caller guarantees is absent from the data, e.g. U+001E).
    /// Returns the flat row-major NxN matrix as a Float64Array.
    #[wasm_bindgen]
    pub fn score_matrix(values: &str, sep: &str, scorer_id: u8) -> Vec<f64> {
        let parts: Vec<&str> = if values.is_empty() {
            Vec::new()
        } else {
            values.split(sep).collect()
        };
        score_matrix_impl(&parts, scorer_id)
    }
}
```

- [ ] **Step 5: Verify it still compiles for the host target + clippy is clean**

Run (with preamble):
```bash
cd packages/rust/extensions/score-wasm && cargo build && cargo clippy -- -D warnings && cargo fmt --check
```
Expected: builds, no clippy warnings, fmt clean. (The `wasm` module is `cfg`-gated out on host — that is expected; it compiles in the wasm build in Task 2.)

- [ ] **Step 6: Add `.gitignore` + commit**

`packages/rust/extensions/score-wasm/.gitignore`:
```
/target
/pkg
Cargo.lock
```

```bash
git add packages/rust/extensions/score-wasm
git commit -m "feat(rust): score-wasm crate wrapping score-core for TS WASM backend"
```

---

## Task 2: WASM build script + artifact wiring

**Files:**
- Create: `packages/rust/extensions/score-wasm/build_wasm.sh`
- Create: `packages/typescript/goldenmatch/src/core/wasm/artifacts/.gitignore`
- Modify: `packages/typescript/goldenmatch/tsup.config.ts`

- [ ] **Step 1: Write the build script**

`packages/rust/extensions/score-wasm/build_wasm.sh`:
```bash
#!/usr/bin/env bash
# Build score-wasm for wasm32 and copy the artifact + glue into the goldenmatch
# TS package. Run from anywhere. Requires: rustup wasm32 target + wasm-bindgen-cli.
set -euo pipefail
export PATH="/c/Users/bsevern/.cargo/bin:$PATH"
export RUSTUP_HOME="${RUSTUP_HOME:-C:/Users/bsevern/.rustup}"
export CARGO_HOME="${CARGO_HOME:-C:/Users/bsevern/.cargo}"

CRATE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="$CRATE_DIR/../../../typescript/goldenmatch/src/core/wasm/artifacts"

rustup target add wasm32-unknown-unknown
cargo install wasm-bindgen-cli --version 0.2.* 2>/dev/null || true

cargo build --manifest-path "$CRATE_DIR/Cargo.toml" --target wasm32-unknown-unknown --release
wasm-bindgen \
  "$CRATE_DIR/target/wasm32-unknown-unknown/release/goldenmatch_score_wasm.wasm" \
  --target web --out-dir "$OUT_DIR" --out-name score_wasm

echo "Artifact written to $OUT_DIR (score_wasm_bg.wasm + score_wasm.js)"
```

- [ ] **Step 2: Make it executable + ignore the artifacts (keep the dir tracked)**

`packages/typescript/goldenmatch/src/core/wasm/artifacts/.gitignore`:
```
# Built by score-wasm/build_wasm.sh (CI) — never committed.
score_wasm_bg.wasm
score_wasm.js
score_wasm.d.ts
score_wasm_bg.wasm.d.ts
```

```bash
chmod +x packages/rust/extensions/score-wasm/build_wasm.sh
```

- [ ] **Step 3: Run the build script — verify the artifact lands**

Run:
```bash
bash packages/rust/extensions/score-wasm/build_wasm.sh && ls -la packages/typescript/goldenmatch/src/core/wasm/artifacts/
```
Expected: `score_wasm_bg.wasm` + `score_wasm.js` present. (If the wasm toolchain is unavailable locally, this runs in CI — Task 8 — and the rest of the TS tasks below that don't need the artifact still proceed.)

- [ ] **Step 4: Wire tsup to copy the artifact into `dist`**

Modify `packages/typescript/goldenmatch/tsup.config.ts` — add after the `entry` block (so the loader's `new URL('../artifacts/score_wasm_bg.wasm', import.meta.url)` resolves in `dist`):
```ts
  // Copy the opt-in WASM artifact (built by score-wasm/build_wasm.sh) into dist
  // so the universal loader can resolve it at runtime. Absent in a default
  // checkout — enableWasm() then returns false and pure-TS is used.
  loader: { ".wasm": "copy" },
  publicDir: false,
```
And in the same file, add an `onSuccess` copy (tsup does not bundle `import.meta.url` assets automatically):
```ts
  onSuccess: "node scripts/copy_wasm_artifact.mjs",
```
Create `packages/typescript/goldenmatch/scripts/copy_wasm_artifact.mjs`:
```js
// Copy the built WASM artifact from src into dist next to the loader output.
// No-op (warns) when the artifact is absent (default checkout / no toolchain).
import { cp, mkdir, access } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const src = join(here, "..", "src", "core", "wasm", "artifacts");
const dst = join(here, "..", "dist", "core", "wasm", "artifacts");
const files = ["score_wasm_bg.wasm", "score_wasm.js"];
try {
  await access(join(src, files[0]));
} catch {
  console.warn("[copy_wasm_artifact] no WASM artifact in src — skipping (pure-TS default).");
  process.exit(0);
}
await mkdir(dst, { recursive: true });
for (const f of files) await cp(join(src, f), join(dst, f));
console.log("[copy_wasm_artifact] copied", files.join(", "), "->", dst);
```

- [ ] **Step 5: Commit**

```bash
git add packages/rust/extensions/score-wasm/build_wasm.sh \
        packages/typescript/goldenmatch/src/core/wasm/artifacts/.gitignore \
        packages/typescript/goldenmatch/tsup.config.ts \
        packages/typescript/goldenmatch/scripts/copy_wasm_artifact.mjs
git commit -m "build(ts): score-wasm build script + tsup artifact copy"
```

---

## Task 3: `ScorerBackend` interface + module singleton

**Files:**
- Create: `packages/typescript/goldenmatch/src/core/wasm/backend.ts`
- Test: `packages/typescript/goldenmatch/tests/unit/wasm-backend.test.ts`

- [ ] **Step 1: Write the failing test**

`tests/unit/wasm-backend.test.ts`:
```ts
import { describe, it, expect, afterEach } from "vitest";
import {
  setScorerBackend,
  getScorerBackend,
  WASM_COVERED_SCORERS,
  type ScorerBackend,
} from "../../src/core/wasm/backend.js";

const stub: ScorerBackend = {
  scoreMatrix: (values) => new Float64Array(values.length * values.length),
};

describe("ScorerBackend singleton", () => {
  afterEach(() => setScorerBackend(null));

  it("defaults to no backend", () => {
    expect(getScorerBackend()).toBeNull();
  });

  it("registers and clears a backend", () => {
    setScorerBackend(stub);
    expect(getScorerBackend()).toBe(stub);
    setScorerBackend(null);
    expect(getScorerBackend()).toBeNull();
  });

  it("covers exactly jaro_winkler / levenshtein / exact in slice 1", () => {
    expect([...WASM_COVERED_SCORERS].sort()).toEqual(
      ["exact", "jaro_winkler", "levenshtein"],
    );
  });
});
```

- [ ] **Step 2: Run it — verify it fails**

Run: `cd packages/typescript/goldenmatch && npx vitest run tests/unit/wasm-backend.test.ts`
Expected: FAIL — cannot resolve `../../src/core/wasm/backend.js`.

- [ ] **Step 3: Write `backend.ts`**

`src/core/wasm/backend.ts`:
```ts
/**
 * backend.ts — opt-in WASM scorer backend registry. Edge-safe: no node:* here.
 *
 * The active backend (if any) is consulted by scorer.ts's `scoreMatrix` for the
 * COVERED scorers only; everything else stays pure-TS. Mirrors the
 * setSyncEmbedder(null) module-singleton pattern for test isolation.
 */

/** Scorer ids understood by the score-wasm kernel (match score-core::score_one). */
export const SCORER_ID: Readonly<Record<string, number>> = {
  jaro_winkler: 0,
  levenshtein: 1,
  exact: 3,
};

/**
 * Scorers the WASM matrix path accelerates in slice 1. token_sort (id 2) is
 * deferred — its normalization parity is unresolved (see the design spec).
 */
export const WASM_COVERED_SCORERS: ReadonlySet<string> = new Set(
  Object.keys(SCORER_ID),
);

/** A WASM-backed (or stub) NxN matrix scorer. Null handling is the caller's. */
export interface ScorerBackend {
  /** Row-major NxN similarity matrix for `values` under `scorerName`. */
  scoreMatrix(values: readonly string[], scorerName: string): Float64Array;
}

let _backend: ScorerBackend | null = null;

export function setScorerBackend(b: ScorerBackend | null): void {
  _backend = b;
}

export function getScorerBackend(): ScorerBackend | null {
  return _backend;
}
```

- [ ] **Step 4: Run it — verify it passes**

Run: `npx vitest run tests/unit/wasm-backend.test.ts`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add packages/typescript/goldenmatch/src/core/wasm/backend.ts \
        packages/typescript/goldenmatch/tests/unit/wasm-backend.test.ts
git commit -m "feat(ts): ScorerBackend interface + covered-scorer registry"
```

---

## Task 4: Make `scoreMatrix` backend-aware (per-scorer swap)

**Files:**
- Modify: `packages/typescript/goldenmatch/src/core/scorer.ts` (the exported `scoreMatrix`, ~line 488)
- Test: `packages/typescript/goldenmatch/tests/unit/wasm-backend.test.ts` (extend)

> **Pipeline reality (read first):** the production hot path `findFuzzyMatches`
> calls the *internal* `buildScoreMatrix` (scorer.ts:592), whose `switch`
> intercepts `exact`→`exactScoreMatrix` (O(n) hash), `soundex_match`, and
> `ensemble` **before** they reach `scoreMatrix`; only the `default` branch
> delegates to `scoreMatrix(values, scorerName)`. So in the real dedupe
> pipeline the WASM swap is effective for **jaro_winkler / levenshtein** (the
> `default` branch). `exact` is still WASM-covered by `score_one(3,…)` and is
> exercised by the parity test (which calls `scoreMatrix` directly), but `exact`
> dedupe runs keep using the faster O(n) hash path — that's intended, not a gap.
> We make the *exported* `scoreMatrix` backend-aware (single swap point;
> `buildScoreMatrix`'s default branch routes through it automatically).

- [ ] **Step 1: Write the failing test (stub backend routes covered scorers only)**

Append to `tests/unit/wasm-backend.test.ts`:
```ts
import { scoreMatrix } from "../../src/core/scorer.js";

describe("scoreMatrix backend swap", () => {
  afterEach(() => setScorerBackend(null));

  it("routes a COVERED scorer through the backend", () => {
    const calls: string[] = [];
    setScorerBackend({
      scoreMatrix: (values, name) => {
        calls.push(name);
        return new Float64Array(values.length * values.length); // all zeros
      },
    });
    const m = scoreMatrix(["abc", "abd"], "jaro_winkler");
    expect(calls).toEqual(["jaro_winkler"]);
    expect(m[0]![1]).toBe(0); // came from the stub, not pure-TS (~0.9)
  });

  it("ignores the backend for an UNCOVERED scorer (token_sort stays pure-TS)", () => {
    let called = false;
    setScorerBackend({
      scoreMatrix: (values) => {
        called = true;
        return new Float64Array(values.length * values.length);
      },
    });
    const m = scoreMatrix(["a b", "b a"], "token_sort");
    expect(called).toBe(false);
    expect(m[0]![1]).toBeGreaterThan(0.99); // pure-TS token_sort ~1.0
  });

  it("zeros out null cells after a backend call", () => {
    setScorerBackend({
      scoreMatrix: (values) => new Float64Array(values.length * values.length).fill(1),
    });
    const m = scoreMatrix(["abc", null], "jaro_winkler");
    expect(m[0]![1]).toBe(0); // null cell masked to 0 despite backend returning 1
  });
});
```

- [ ] **Step 2: Run it — verify it fails**

Run: `npx vitest run tests/unit/wasm-backend.test.ts`
Expected: FAIL — `scoreMatrix` currently ignores the backend.

- [ ] **Step 3: Modify `scoreMatrix` in `scorer.ts`**

Add the import near the top of `scorer.ts` (with the other `./` imports):
```ts
import { getScorerBackend, WASM_COVERED_SCORERS } from "./wasm/backend.js";
```
Replace the body of the exported `scoreMatrix` (currently the plain double loop, ~lines 488-502) with:
```ts
export function scoreMatrix(
  values: (string | null)[],
  scorerName: string,
): number[][] {
  const n = values.length;
  const backend = getScorerBackend();

  // Opt-in WASM fast path: ONE boundary crossing per NxN block, covered
  // scorers only. Nulls are masked to 0 here (the backend never sees them).
  if (backend !== null && WASM_COVERED_SCORERS.has(scorerName)) {
    const SEP = "\x1e"; // record-separator; never appears in scored field data
    const clean = values.map((v) => (v ?? "").replaceAll(SEP, ""));
    const flat = backend.scoreMatrix(clean, scorerName);
    const matrix: number[][] = Array.from({ length: n }, () =>
      new Array<number>(n).fill(0),
    );
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        const masked = values[i] === null || values[j] === null;
        const s = masked ? 0 : flat[i * n + j]!;
        matrix[i]![j] = s;
        matrix[j]![i] = s;
      }
    }
    return matrix;
  }

  // Pure-TS default (unchanged).
  const matrix: number[][] = Array.from({ length: n }, () => new Array<number>(n).fill(0));
  for (let i = 0; i < n; i++) {
    for (let j = i + 1; j < n; j++) {
      const s = scoreField(values[i]!, values[j]!, scorerName) ?? 0;
      matrix[i]![j] = s;
      matrix[j]![i] = s;
    }
  }
  return matrix;
}
```

- [ ] **Step 4: Run the unit test + the existing scorer tests — verify pass + no regression**

Run:
```bash
npx vitest run tests/unit/wasm-backend.test.ts tests/parity/scorer-ground-truth.test.ts
```
Expected: all PASS (backend swap works; pure-TS path unchanged when no backend is registered).

- [ ] **Step 5: Typecheck**

Run: `npx tsc --noEmit`
Expected: no errors. (Watch `noUncheckedIndexedAccess` — the `flat[i*n+j]!` and `matrix[i]![j]` non-null assertions are required.)

- [ ] **Step 6: Commit**

```bash
git add packages/typescript/goldenmatch/src/core/scorer.ts \
        packages/typescript/goldenmatch/tests/unit/wasm-backend.test.ts
git commit -m "feat(ts): backend-aware scoreMatrix (per-scorer WASM swap + null mask)"
```

---

## Task 5: Universal loader + `enableWasm`/`disableWasm`

**Files:**
- Create: `packages/typescript/goldenmatch/src/core/wasm/loader.ts`
- Create: `packages/typescript/goldenmatch/src/core/wasm/index.ts`
- Modify: `packages/typescript/goldenmatch/src/core/index.ts`
- Test: `packages/typescript/goldenmatch/tests/unit/wasm-fallback.test.ts`

- [ ] **Step 1: Write the failing fallback test**

`tests/unit/wasm-fallback.test.ts`:
```ts
import { describe, it, expect, afterEach } from "vitest";
import { enableWasm, disableWasm } from "../../src/core/wasm/index.js";
import { getScorerBackend } from "../../src/core/wasm/backend.js";
import { scoreField } from "../../src/core/index.js";

describe("enableWasm graceful fallback", () => {
  afterEach(() => disableWasm());

  it("returns false and leaves pure-TS active when no artifact + no override", async () => {
    // Force the no-bytes path with an override that yields nothing.
    const ok = await enableWasm({ wasmBytes: new Uint8Array(0) });
    expect(ok).toBe(false);
    expect(getScorerBackend()).toBeNull();
    // Scoring still works (pure-TS).
    expect(scoreField("abc", "abc", "jaro_winkler")).toBe(1.0);
  });

  it("throws when require:true and bytes are unusable", async () => {
    await expect(
      enableWasm({ wasmBytes: new Uint8Array(0), require: true }),
    ).rejects.toThrow();
  });

  it("disableWasm resets to pure-TS", async () => {
    await enableWasm({ wasmBytes: new Uint8Array(0) });
    disableWasm();
    expect(getScorerBackend()).toBeNull();
  });
});
```

- [ ] **Step 2: Run it — verify it fails**

Run: `npx vitest run tests/unit/wasm-fallback.test.ts`
Expected: FAIL — `../../src/core/wasm/index.js` unresolved.

- [ ] **Step 3: Write `loader.ts`**

`src/core/wasm/loader.ts`:
```ts
/**
 * loader.ts — universal WASM byte loader + instantiation. Edge-safe: the only
 * node:* touch is a guarded dynamic `import("node:fs/promises" as string)`, the
 * documented idiom that keeps tsup from statically resolving node built-ins.
 *
 * Resolution order: explicit bytes → explicit URL → fs (Node) → fetch
 * (browser/Workers/bundler). Any failure throws; index.ts turns that into the
 * pure-TS fallback (or rethrows under { require: true }).
 */
import type { ScorerBackend } from "./backend.js";

export interface LoadOptions {
  readonly wasmBytes?: Uint8Array;
  readonly wasmUrl?: string | URL;
}

/** Resolve the raw wasm bytes for the current environment. */
export async function resolveWasmBytes(opts: LoadOptions): Promise<Uint8Array> {
  if (opts.wasmBytes !== undefined) {
    if (opts.wasmBytes.byteLength === 0) throw new Error("empty wasmBytes");
    return opts.wasmBytes;
  }
  const url =
    opts.wasmUrl ?? new URL("./artifacts/score_wasm_bg.wasm", import.meta.url);

  const isNode =
    typeof process !== "undefined" &&
    process.versions?.node !== undefined &&
    (url instanceof URL ? url.protocol === "file:" : String(url).startsWith("file:"));

  if (isNode) {
    const fs = await import("node:fs/promises" as string);
    const buf = await fs.readFile(url as URL);
    return new Uint8Array(buf.buffer, buf.byteOffset, buf.byteLength);
  }
  const resp = await fetch(url as URL);
  if (!resp.ok) throw new Error(`fetch wasm failed: ${resp.status}`);
  return new Uint8Array(await resp.arrayBuffer());
}

/**
 * Instantiate the score-wasm module and adapt it to a ScorerBackend. Uses the
 * wasm-bindgen `--target web` glue (default export = init(bytes)).
 */
export async function instantiateBackend(bytes: Uint8Array): Promise<ScorerBackend> {
  // Dynamic import of the generated glue (absent in a default checkout).
  const glue = (await import("./artifacts/score_wasm.js" as string)) as {
    default: (input: { module_or_path: BufferSource }) => Promise<unknown>;
    score_matrix: (values: string, sep: string, scorerId: number) => Float64Array;
  };
  await glue.default({ module_or_path: bytes });

  const SEP = "\x1e";
  const idOf: Record<string, number> = { jaro_winkler: 0, levenshtein: 1, exact: 3 };
  return {
    scoreMatrix(values: readonly string[], scorerName: string): Float64Array {
      const id = idOf[scorerName];
      if (id === undefined) throw new Error(`uncovered scorer: ${scorerName}`);
      return glue.score_matrix(values.join(SEP), SEP, id);
    },
  };
}
```

- [ ] **Step 4: Write `index.ts` (the public opt-in API)**

`src/core/wasm/index.ts`:
```ts
/**
 * Public opt-in WASM API. enableWasm() is async (browsers ban sync instantiation
 * >4KB); after it resolves, the existing SYNC scoreMatrix runs against the
 * instantiated module. Pure-TS stays the default + fallback.
 */
import { setScorerBackend } from "./backend.js";
import type { LoadOptions } from "./loader.js";

export type { ScorerBackend } from "./backend.js";
export { WASM_COVERED_SCORERS } from "./backend.js";

export interface EnableWasmOptions extends LoadOptions {
  /** Throw instead of falling back to pure-TS when the module can't load. */
  readonly require?: boolean;
}

let _enabled = false;

/**
 * Load + instantiate the WASM scorer backend and register it. Returns true on
 * success. On failure returns false (pure-TS stays active) unless require:true.
 * Idempotent while a backend is active.
 */
export async function enableWasm(opts: EnableWasmOptions = {}): Promise<boolean> {
  if (_enabled) return true;
  try {
    // Lazy: default (pure-TS) users never load the loader/glue/bytes.
    const { resolveWasmBytes, instantiateBackend } = await import("./loader.js");
    const bytes = await resolveWasmBytes(opts);
    const backend = await instantiateBackend(bytes);
    setScorerBackend(backend);
    _enabled = true;
    return true;
  } catch (err) {
    if (opts.require) throw err;
    return false;
  }
}

/** Reset to pure-TS (test isolation; mirrors setSyncEmbedder(null)). */
export function disableWasm(): void {
  setScorerBackend(null);
  _enabled = false;
}
```

- [ ] **Step 5: Export from the core barrel**

Modify `src/core/index.ts` — add after the scorer export block (after line ~118):
```ts
export { enableWasm, disableWasm, WASM_COVERED_SCORERS } from "./wasm/index.js";
export type { ScorerBackend, EnableWasmOptions } from "./wasm/index.js";
```
(`EnableWasmOptions` and `ScorerBackend` are both exported from `wasm/index.ts`, so this re-export resolves directly.)

- [ ] **Step 6: Run the fallback test — verify it passes**

Run: `npx vitest run tests/unit/wasm-fallback.test.ts`
Expected: PASS (3 tests). The empty-bytes override forces the failure path without needing a real artifact.

- [ ] **Step 7: Typecheck**

Run: `npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add packages/typescript/goldenmatch/src/core/wasm/loader.ts \
        packages/typescript/goldenmatch/src/core/wasm/index.ts \
        packages/typescript/goldenmatch/src/core/index.ts \
        packages/typescript/goldenmatch/tests/unit/wasm-fallback.test.ts
git commit -m "feat(ts): enableWasm/disableWasm opt-in API + universal loader"
```

---

## Task 6: CI-gated parity test (WASM ≈ pure-TS ≈ Python goldens)

**Files:**
- Create: `packages/typescript/goldenmatch/tests/parity/wasm-scorer.test.ts`

- [ ] **Step 1: Write the parity test (skip-guarded on the artifact)**

`tests/parity/wasm-scorer.test.ts`:
```ts
/**
 * WASM-vs-pure-TS-vs-Python parity for the COVERED scorers. Skipped when the
 * built artifact is absent (default checkout / no toolchain); the CI lane
 * builds it first and runs this un-skipped. 4-decimal tolerance, matching the
 * existing scorer ground-truth contract.
 */
import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { enableWasm, disableWasm } from "../../src/core/wasm/index.js";
import { scoreMatrix } from "../../src/core/scorer.js";

const artifact = fileURLToPath(
  new URL("../../src/core/wasm/artifacts/score_wasm_bg.wasm", import.meta.url),
);
const hasArtifact = existsSync(artifact);
const d = hasArtifact ? describe : describe.skip;

// Reuse / extend the scorer ground-truth corpus (inline CASES live in
// scorer-ground-truth.test.ts). Cover jaro_winkler/levenshtein/exact + non-BMP.
const VALUES = [
  "MARTHA", "MARHTA", "DIXON", "DICKSONX", "John", "Jon",
  "kitten", "sitting", "saturday", "sunday", "abc", "abd",
  "café", "cafe", "😀ab", "😀ac", "", "x",
];
const SCORERS = ["jaro_winkler", "levenshtein", "exact"] as const;

d("WASM scorer parity", () => {
  afterAll(() => disableWasm());

  for (const scorer of SCORERS) {
    it(`${scorer}: WASM matrix matches pure-TS matrix (4dp)`, async () => {
      disableWasm();
      const pure = scoreMatrix(VALUES, scorer); // pure-TS
      const ok = await enableWasm();
      expect(ok).toBe(true); // artifact present in this lane
      const wasm = scoreMatrix(VALUES, scorer); // backend active
      disableWasm();
      for (let i = 0; i < VALUES.length; i++) {
        for (let j = 0; j < VALUES.length; j++) {
          expect(wasm[i]![j]!).toBeCloseTo(pure[i]![j]!, 4);
        }
      }
    });
  }
});
```
> Each `it` is self-contained: capture pure-TS first, enable, capture WASM,
> disable, compare. No idempotency interaction to reason about. The REQUIREMENT:
> for each covered scorer, WASM matrix ≈ pure-TS matrix to 4dp over a corpus that
> includes non-BMP (`😀`) and accented (`café`) inputs.

- [ ] **Step 2: Run it locally — verify it SKIPS (no artifact) or PASSES (artifact built)**

Run: `npx vitest run tests/parity/wasm-scorer.test.ts`
Expected (no artifact): the suite is `describe.skip` → reported skipped, exit 0. (After `build_wasm.sh`: runs and PASSES.)

- [ ] **Step 3: Commit**

```bash
git add packages/typescript/goldenmatch/tests/parity/wasm-scorer.test.ts
git commit -m "test(ts): CI-gated WASM scorer parity (skips without artifact)"
```

---

## Task 7: Benchmark (the measure-first graduation gate)

**Files:**
- Create: `packages/typescript/goldenmatch/scripts/bench_wasm_scorer.mjs`

- [ ] **Step 1: Write the bench**

`scripts/bench_wasm_scorer.mjs`:
```js
// 5-run median wall: pure-TS vs WASM scoreMatrix on a realistic NxN block.
// Graduation gate: a core ships WASM acceleration only if WASM measurably wins.
// Run AFTER build_wasm.sh. Usage: node scripts/bench_wasm_scorer.mjs [N]
import { scoreMatrix } from "../dist/core/index.js";
import { enableWasm, disableWasm } from "../dist/core/index.js";

const N = Number(process.argv[2] ?? 1500);
const SCORER = "jaro_winkler";
const rnd = (seed) => () => ((seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff);
const r = rnd(42);
const pick = "abcdefghijklmnopqrstuvwxyz ";
const mkStr = () => Array.from({ length: 6 + ((r() * 8) | 0) }, () => pick[(r() * pick.length) | 0]).join("");
const values = Array.from({ length: N }, mkStr);

const median = (xs) => xs.slice().sort((a, b) => a - b)[Math.floor(xs.length / 2)];
function time(fn) {
  const runs = [];
  for (let k = 0; k < 5; k++) {
    const t0 = performance.now();
    fn();
    runs.push(performance.now() - t0);
  }
  return median(runs);
}

disableWasm();
const pureMs = time(() => scoreMatrix(values, SCORER));
const ok = await enableWasm();
if (!ok) {
  console.error("WASM artifact not built — run score-wasm/build_wasm.sh first.");
  process.exit(1);
}
const wasmMs = time(() => scoreMatrix(values, SCORER));
disableWasm();

console.log(`N=${N} scorer=${SCORER}`);
console.log(`pure-TS : ${pureMs.toFixed(1)} ms (median of 5)`);
console.log(`WASM    : ${wasmMs.toFixed(1)} ms (median of 5)`);
console.log(`speedup : ${(pureMs / wasmMs).toFixed(2)}x`);
```
> Imports from `dist/` so it runs against the built artifact-copied output. Build first: `npm run build` (after `build_wasm.sh`).

- [ ] **Step 2: Commit** (running it requires the artifact + CI; the CI lane invokes it)

```bash
git add packages/typescript/goldenmatch/scripts/bench_wasm_scorer.mjs
git commit -m "bench(ts): pure-TS vs WASM scoreMatrix wall-clock"
```

---

## Task 8: CI lane (build wasm → parity un-skipped → bench)

**Files:**
- Modify: `.github/workflows/ci.yml` (new `changes` filter entry + new job)

- [ ] **Step 1: Add the path-filter entry**

In `.github/workflows/ci.yml`, find the `changes` job's `filters:` block and add:
```yaml
            wasm_score:
              - 'packages/rust/extensions/score-wasm/**'
              - 'packages/rust/extensions/score-core/**'
              - 'packages/typescript/goldenmatch/src/core/wasm/**'
              - 'packages/typescript/goldenmatch/src/core/scorer.ts'
              - '.github/workflows/ci.yml'
```
And add `wasm_score` to the `changes` job's `outputs:` map (mirror an existing `<area>: ${{ steps.filter.outputs.<area> }}` line).

- [ ] **Step 2: Add the job (gated on the filter)**

Append a job (mirror the structure of the existing TS job; gate with `if:`):
```yaml
  wasm-score:
    needs: changes
    if: needs.changes.outputs.wasm_score == 'true'
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: packages/typescript/goldenmatch
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable
        with:
          targets: wasm32-unknown-unknown
      - name: Install wasm-bindgen-cli
        run: cargo install wasm-bindgen-cli --version '0.2.*'
      - uses: pnpm/action-setup@v4
        with:
          version: 9.15.0
      - uses: actions/setup-node@v4
        with:
          node-version: 20
      - name: Build WASM artifact
        working-directory: .
        run: bash packages/rust/extensions/score-wasm/build_wasm.sh
      - name: Install + build TS
        # --frozen-lockfile: the wasm work adds no npm deps, so the lockfile
        # must not change — stay consistent with the repo's other CI lanes.
        run: pnpm install --frozen-lockfile && pnpm build
      - name: WASM parity (un-skipped)
        run: npx vitest run tests/parity/wasm-scorer.test.ts
      - name: Benchmark (informational)
        run: node scripts/bench_wasm_scorer.mjs 1500
```
> Match the repo's existing TS job for the exact pnpm/turbo invocation and lockfile flags (root CLAUDE.md: single TS job, `pnpm-lock.yaml` committed). If the repo uses `pnpm turbo run build`, mirror that instead of `pnpm build`. The `working-directory: .` override on the build-wasm step is needed because the script path is repo-relative.

- [ ] **Step 3: Validate the workflow YAML**

Run: `python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml')); print('ok')"`
Expected: `ok`. (A new job REQUIRES both the filter entry AND the `if:` gate — root CLAUDE.md rule.)

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: wasm-score lane (build wasm, run parity un-skipped, bench)"
```

---

## Task 9: Docs + changelog

**Files:**
- Modify: `packages/typescript/goldenmatch/CLAUDE.md`, `README.md`, `CHANGELOG.md`

- [ ] **Step 1: CLAUDE.md — document the opt-in path + build step**

Add a section to `packages/typescript/goldenmatch/CLAUDE.md`:
```markdown
## Opt-in WASM scorer (score-wasm)
- `await enableWasm()` swaps a WASM backend (wrapping the Rust `score-core`
  crate via `packages/rust/extensions/score-wasm/`) behind the sync `scoreMatrix`
  for COVERED scorers only: `jaro_winkler`/`levenshtein`/`exact`. Everything else
  (incl. `token_sort` — normalization parity unresolved) stays pure-TS.
- Pure-TS is the default + fallback. enableWasm() returns false (pure-TS stays)
  on any load failure; `{ require: true }` throws. `disableWasm()` resets.
- The `.wasm` is NOT committed. Build locally: `bash packages/rust/extensions/
  score-wasm/build_wasm.sh` (needs the rustup wasm32 target + wasm-bindgen-cli),
  then `npm run build`. CI's `wasm-score` lane builds it and runs the parity test
  un-skipped; without the artifact `tests/parity/wasm-scorer.test.ts` skips.
- Swap happens at the NxN matrix boundary, never per-pair (boundary cost).
```

- [ ] **Step 2: README.md — a short "Optional WASM acceleration" note** (mirror the CLAUDE wording, user-facing: `await enableWasm()` before `dedupe`).

- [ ] **Step 3: CHANGELOG.md — add an Unreleased entry**
```markdown
### Unreleased
- Opt-in WASM scorer backend (`enableWasm`/`disableWasm`) wrapping the Rust
  `score-core` crate. jaro_winkler/levenshtein/exact only; pure-TS default +
  fallback; edge/browser/Node. Parity-gated in CI (`wasm-score` lane).
```

- [ ] **Step 4: Typecheck + the two artifact-free test files one last time**

Run:
```bash
npx tsc --noEmit && npx vitest run tests/unit/wasm-backend.test.ts tests/unit/wasm-fallback.test.ts
```
Expected: clean typecheck + all PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/typescript/goldenmatch/CLAUDE.md \
        packages/typescript/goldenmatch/README.md \
        packages/typescript/goldenmatch/CHANGELOG.md
git commit -m "docs(ts): document opt-in WASM scorer backend"
```

---

## Done-when (slice 1 acceptance)

- `score-wasm` crate builds for `wasm32-unknown-unknown`; `cargo test`/`clippy`/`fmt` clean on host.
- `enableWasm()` registers a backend; default + failure paths stay pure-TS; `disableWasm()` resets.
- Pure-TS scoring is byte-unchanged when WASM is not enabled (existing scorer goldens still green).
- `wasm-score` CI lane: builds the artifact, parity test runs **un-skipped** and passes (WASM ≈ pure-TS ≈ Python, 4dp, incl. non-BMP), bench prints a speedup number.
- Default-checkout `npm run build` + the full vitest suite are unaffected (parity test skips; no new required dep).
- PR opened off `feat/wasm-opt-in-score`; merge-on-green per `feedback_branch_merge_sop`.

## Out of scope (own bench-gated specs later)

graph-core → `cluster.ts`; analysis-core → `aggregate.ts`; token_sort WASM coverage (after resolving its normalization parity); fingerprint-core + goldencheck-core (parked); the shared-runtime-package extraction.
