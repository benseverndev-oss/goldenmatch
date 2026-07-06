# InferMap WASM/TS Wave A — Implementation Plan (foundation + `detect_domain`)

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the opt-in-WASM pipeline for the TS `infermap` package (a new `infermap-wasm` wasm-bindgen crate over `infermap-core`, TS `wasm/{backend,loader,index}.ts`, build script, CI lane, byte-parity gate) and wire `detect_domain` through it — making TS run the same Rust kernel Python's native wheel runs.

**Architecture:** JSON-over-boundary (crossed once per call): the TS `detect.ts` resolves domain hints host-side into a flat `[name, hints[]][]`, hands it to a WASM backend that JSON-round-trips to `infermap_core::detect_domain`, with a pure-TS `scoreDomains` fallback. CI-built artifact (not committed; parity test skips locally, runs in the `infermap_wasm` lane). WASM is the reference; pure TS is the lossy fallback.

**Tech Stack:** Rust (`infermap-wasm` cdylib+rlib, `serde`/`serde_json`, `wasm-bindgen`), `wasm-bindgen-cli`, TS/tsup, `goldenmatch-wasm-runtime`, vitest.

**Spec:** `docs/superpowers/specs/2026-07-06-infermap-wasm-wave-a-design.md`

**Reference skill:** @superpowers:test-driven-development

---

## Environment & Constraints (READ FIRST)

**Repo:** `D:\show_case\gg-local-llm`, branch `feat/infermap-wasm-wave-a` (checked out, spec committed).

**THE BOX CAN RUN ALMOST NOTHING HERE.** Per `feedback_box_memory_oom_ts` + the Rust-CI-only rule:
- **NO `cargo build` / `cargo test` / `wasm-pack` / `wasm-bindgen`** — Rust + wasm are CI-only.
- **NO `vitest` / `tsc` / `tsup` / `pnpm build`** — TS OOM-kills the box; CI-only.
- **What the box CAN do:** `node --check <file.mjs>` (syntax-check `.mjs`), `git`, `python -c` for JSON/text checks, read/grep, and careful eye-review against the exemplars named per task.

So every task is **write-against-spec + syntax/eye-verify + commit**; **CI is the first real test.** Be precise. When a step says "verify by eye," compare against the named real exemplar file line-by-line.

**Exemplars to mirror (read them):**
- Crate: `packages/rust/extensions/score-wasm/{Cargo.toml,src/lib.rs,build_wasm.sh}`, `packages/rust/extensions/analysis-wasm/src/lib.rs`.
- TS wasm module: `packages/typescript/goldenanalysis/src/core/wasm/{backend,loader,index}.ts`.
- tsup: `packages/typescript/goldenanalysis/tsup.config.ts`.
- copy script: `packages/typescript/goldenanalysis/scripts/copy_wasm_artifact.mjs`.
- parity test: `packages/typescript/goldenanalysis/tests/parity/wasm-aggregate.test.ts`.
- CI lane: `.github/workflows/ci.yml` `analysis_wasm` job (~line 1904) + paths-filter (~line 234).
- artifact gitignore: `packages/typescript/goldenanalysis/src/core/wasm/artifacts/.gitignore`.

**Git:** benzsevern (`unset GH_TOKEN; gh auth switch --user benzsevern; export GH_TOKEN=$(gh auth token --user benzsevern)`). Merge-queue — `gh pr merge --auto --squash` WITHOUT `--delete-branch`. Commit trailer:
```
Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44
```

---

## File Structure

| File | Responsibility | Action |
| --- | --- | --- |
| `packages/rust/extensions/infermap-wasm/Cargo.toml` | standalone crate, serde+wasm-bindgen | Create |
| `packages/rust/extensions/infermap-wasm/src/lib.rs` | `detect_domain_json_impl` + wasm re-export + host test | Create |
| `packages/rust/extensions/infermap-wasm/build_wasm.sh` | cargo+wasm-bindgen build → TS artifacts | Create |
| `packages/rust/extensions/infermap-wasm/Cargo.lock` | pins wasm-bindgen (committed) | Create (CI/generated note) |
| `packages/typescript/infermap/src/core/wasm/backend.ts` | `InfermapBackend` iface + registry | Create |
| `packages/typescript/infermap/src/core/wasm/loader.ts` | glue → backend adapter | Create |
| `packages/typescript/infermap/src/core/wasm/index.ts` | `enable/disableInfermapWasm` | Create |
| `packages/typescript/infermap/src/core/wasm/artifacts/.gitignore` | ignore built artifacts | Create |
| `packages/typescript/infermap/src/core/detect.ts` | hoist refactor + `scoreDomains` + backend dispatch | Modify |
| `packages/typescript/infermap/package.json` | add `goldenmatch-wasm-runtime` devDep | Modify |
| `packages/typescript/infermap/tsup.config.ts` | artifact-shipping keys | Modify |
| `packages/typescript/infermap/scripts/copy_wasm_artifact.mjs` | fan artifact to dist | Create |
| `packages/typescript/infermap/tests/parity/infermap-wasm.parity.test.ts` | WASM-vs-pure gate | Create |
| `.github/workflows/ci.yml` | `infermap_wasm` lane + filter | Modify |

---

## Task 1: The `infermap-wasm` crate

**Files:** Create `packages/rust/extensions/infermap-wasm/Cargo.toml`, `.../src/lib.rs`.
**Box:** eye-review only (no cargo). CI compiles + runs the host unit test.

- [ ] **Step 1: `Cargo.toml`** (mirror `score-wasm/Cargo.toml`, add serde):
```toml
# Standalone workspace so this wasm-bindgen wrapper can path-depend on the
# pyo3-free `infermap-core` WITHOUT either crate's workspace claiming it. This
# crate is the TS analogue of `infermap-native` (pyo3): it wraps the SAME
# `infermap-core`, so detect is byte-identical by construction. serde lives HERE
# (the JSON boundary), never in infermap-core.
[workspace]

[package]
name = "infermap-wasm"
version = "0.1.0"
edition = "2021"
license = "MIT"
authors = ["Ben Severn <benzsevern@gmail.com>"]
description = "wasm-bindgen wrapper over infermap-core for the infermap TS opt-in WASM backend"

[lib]
name = "infermap_wasm"
crate-type = ["cdylib", "rlib"]  # cdylib for wasm; rlib so host unit tests link

[dependencies]
infermap-core = { path = "../infermap-core" }
serde = { version = "1", features = ["derive"] }
serde_json = "1"

[target.'cfg(target_arch = "wasm32")'.dependencies]
wasm-bindgen = "0.2"
```

- [ ] **Step 2: `src/lib.rs`**:
```rust
//! wasm-bindgen wrapper over `infermap-core`. The TS analogue of the
//! `infermap-native` pyo3 crate: a thin JSON-boundary shim delegating to
//! `detect_domain` so the TS surface is byte-identical to Python + the Rust FFI.
//!
//! Boundary: `detect_domain_json(input_json) -> output_json`, crossed ONCE per
//! call (the perf-audit lesson: boundary cost dwarfs the kernel). serde DTOs live
//! here; `infermap-core` stays serde-free.

use serde::{Deserialize, Serialize};

#[derive(Deserialize)]
struct DetectInput {
    columns: Vec<String>,
    domains: Vec<(String, Vec<String>)>,
    min_score: f64,
}

#[derive(Serialize)]
struct DetectOutput {
    domain: Option<String>,
    score: f64,
    runner_up: Option<String>,
    runner_up_score: f64,
    reason: String,
}

/// Host-testable core of the boundary. Parses the resolved detect input, calls
/// the pyo3-free kernel, serializes the Detection. Panics on malformed JSON
/// (the TS caller always sends well-formed input built from typed values).
pub fn detect_domain_json_impl(input_json: &str) -> String {
    let inp: DetectInput =
        serde_json::from_str(input_json).expect("valid detect input json");
    let d = infermap_core::detect_domain(&inp.columns, &inp.domains, inp.min_score);
    let out = DetectOutput {
        domain: d.domain,
        score: d.score,
        runner_up: d.runner_up,
        runner_up_score: d.runner_up_score,
        reason: d.reason,
    };
    serde_json::to_string(&out).expect("serialize detect output")
}

// wasm-only surface: the free `detect_domain_json` export the TS glue calls.
// Mirrors analysis-wasm's `#[cfg(target_arch="wasm32")] mod` re-export.
#[cfg(target_arch = "wasm32")]
mod wasm {
    use wasm_bindgen::prelude::*;

    #[wasm_bindgen]
    pub fn detect_domain_json(input_json: &str) -> String {
        super::detect_domain_json_impl(input_json)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn confident_detect_round_trips() {
        let input = r#"{"columns":["provider_npi","first_name"],
            "domains":[["health",["provider npi"]],["fin",["iban"]]],"min_score":0.3}"#;
        let out = detect_domain_json_impl(input);
        // health scores 1/2=0.5 (provider_npi matches), fin 0 -> confident health.
        assert!(out.contains(r#""domain":"health""#));
        assert!(out.contains(r#""reason":"confident""#));
    }

    #[test]
    fn empty_columns_is_no_data() {
        let input = r#"{"columns":[],"domains":[["h",["x"]]],"min_score":0.3}"#;
        let out = detect_domain_json_impl(input);
        assert!(out.contains(r#""reason":"no_data""#));
        assert!(out.contains(r#""domain":null"#));
    }
}
```

- [ ] **Step 3: Eye-verify (NO cargo).** Confirm against `score-wasm/src/lib.rs` + `analysis-wasm/src/lib.rs`: the `#[cfg(target_arch="wasm32")] mod wasm` re-export shape; the DTO fields match the real `Detection` struct (read `infermap-core/src/lib.rs`: `domain: Option<String>, score: f64, runner_up: Option<String>, runner_up_score: f64, reason: String`); the call is `infermap_core::detect_domain(&inp.columns, &inp.domains, inp.min_score)`. Confirm `serde`/`serde_json` are NOT added to `infermap-core`.

- [ ] **Step 4: Commit** (Cargo.lock is generated by CI's first build; do NOT hand-write it — see Task 2 note):
```bash
cd "D:/show_case/gg-local-llm"
git add packages/rust/extensions/infermap-wasm/Cargo.toml packages/rust/extensions/infermap-wasm/src/lib.rs
git commit -m "feat(infermap-wasm): crate scaffold + detect_domain_json boundary (Wave A)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

**Report:** `DONE`/`BLOCKED`, SHA, and confirm infermap-core untouched.

> **Cargo.lock note:** the crate needs a committed `Cargo.lock` so `build_wasm.sh`
> can read the pinned `wasm-bindgen` version. The box can't run `cargo generate-lockfile`.
> Task 2 handles this: the lockfile is generated in CI on first run and committed,
> OR (fallback) hand-authored minimally. Flag it for the controller; do NOT block Task 1.

---

## Task 2: `build_wasm.sh` + artifact gitignore

**Files:** Create `packages/rust/extensions/infermap-wasm/build_wasm.sh`, `packages/typescript/infermap/src/core/wasm/artifacts/.gitignore`.
**Box:** eye-review + `bash -n` syntax check only.

- [ ] **Step 1: `build_wasm.sh`** — mirror `score-wasm/build_wasm.sh` MINUS its base64 universal-loader tail (spec §2 rejects it). Out-dir = the infermap TS package artifacts; out-name `infermap_wasm`:
```bash
#!/usr/bin/env bash
# Build infermap-wasm for wasm32 and copy the artifact + glue into the infermap
# TS package. Requires: rustup wasm32 target + wasm-bindgen-cli (installed here at
# the version pinned in Cargo.lock — a CLI/crate skew produces broken glue that
# fails at RUNTIME, not build time). Run from anywhere.
set -euo pipefail
export CARGO_HOME="${CARGO_HOME:-$HOME/.cargo}"
export RUSTUP_HOME="${RUSTUP_HOME:-$HOME/.rustup}"
export PATH="$CARGO_HOME/bin:$PATH"

CRATE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="$CRATE_DIR/../../../typescript/infermap/src/core/wasm/artifacts"

rustup target add wasm32-unknown-unknown
cargo build --manifest-path "$CRATE_DIR/Cargo.toml" --target wasm32-unknown-unknown --release

WB_VER="$(grep -A1 '^name = "wasm-bindgen"$' "$CRATE_DIR/Cargo.lock" | grep '^version = ' | head -1 | sed -E 's/version = "([^"]+)"/\1/')"
if [ -z "$WB_VER" ]; then echo "could not resolve wasm-bindgen version from Cargo.lock" >&2; exit 1; fi
echo "Using wasm-bindgen $WB_VER"
if ! wasm-bindgen --version 2>/dev/null | grep -q "$WB_VER"; then
  cargo install wasm-bindgen-cli --version "=$WB_VER" --locked
fi
command -v wasm-bindgen >/dev/null 2>&1 || { echo "wasm-bindgen not on PATH after install; aborting" >&2; exit 1; }

wasm-bindgen \
  "$CRATE_DIR/target/wasm32-unknown-unknown/release/infermap_wasm.wasm" \
  --target web --out-dir "$OUT_DIR" --out-name infermap_wasm

echo "Artifact written to $OUT_DIR (infermap_wasm_bg.wasm + infermap_wasm.js)"
```

- [ ] **Step 2: artifacts `.gitignore`** — mirror the analysis one exactly (create the dir via the file):
`packages/typescript/infermap/src/core/wasm/artifacts/.gitignore`:
```
# Built by infermap-wasm/build_wasm.sh (CI) — never committed.
infermap_wasm_bg.wasm
infermap_wasm.js
infermap_wasm.d.ts
infermap_wasm_bg.wasm.d.ts
```

- [ ] **Step 3: Verify.** `bash -n packages/rust/extensions/infermap-wasm/build_wasm.sh` (syntax OK). Confirm `OUT_DIR` resolves to `packages/typescript/infermap/src/core/wasm/artifacts` from the crate dir (3 `../` up from `extensions/infermap-wasm` → `packages/`, then `typescript/infermap/...`). Confirm the `.gitignore` matches `goldenanalysis/.../artifacts/.gitignore` with `infermap` substituted.

- [ ] **Step 4: Commit:**
```bash
git add packages/rust/extensions/infermap-wasm/build_wasm.sh packages/typescript/infermap/src/core/wasm/artifacts/.gitignore
git commit -m "build(infermap-wasm): build_wasm.sh + artifact gitignore (Wave A)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

**Report:** `DONE`/`BLOCKED`, SHA, and the resolved `OUT_DIR` path.

---

## Task 3: TS wasm module — `backend.ts` / `loader.ts` / `index.ts`

**Files:** Create the three under `packages/typescript/infermap/src/core/wasm/`.
**Box:** eye-review only (no tsc). Mirror `goldenanalysis/src/core/wasm/*` exactly.

- [ ] **Step 1: `backend.ts`:**
```ts
/**
 * backend.ts — opt-in WASM detect backend registry. Edge-safe: no node:* here.
 * Mirrors goldenanalysis's wasm/backend.ts (module-singleton registry).
 */
import { createBackendRegistry } from "goldenmatch-wasm-runtime";
import type { DetectionResult } from "goldencheck-types";

/** A WASM-backed detect kernel. Dictionary resolution stays host; this scores a
 *  resolved [name, hints[]] domain list. */
export interface InfermapBackend {
  detectDomain(
    columns: string[],
    domains: Array<[string, string[]]>,
    minScore: number,
  ): DetectionResult;
}

const _registry = createBackendRegistry<InfermapBackend>();

export function setInfermapBackend(b: InfermapBackend | null): void {
  _registry.set(b);
}

export function getInfermapBackend(): InfermapBackend | null {
  return _registry.get();
}
```

- [ ] **Step 2: `loader.ts`** (mirror analysis loader; adapt the JSON boundary):
```ts
/**
 * loader.ts — instantiate infermap-wasm and adapt it to an InfermapBackend.
 * The wasm-bindgen glue import is dynamic (absent in a default checkout).
 */
import type { DetectionResult } from "goldencheck-types";
import type { InfermapBackend } from "./backend.js";

export async function instantiateBackend(bytes: Uint8Array): Promise<InfermapBackend> {
  const glue = (await import("./artifacts/infermap_wasm.js" as string)) as {
    default: (input: { module_or_path: Uint8Array }) => Promise<unknown>;
    detect_domain_json: (input_json: string) => string;
  };
  await glue.default({ module_or_path: bytes });
  return {
    detectDomain(columns, domains, minScore) {
      // One JSON crossing per call (perf-audit lesson).
      const input = JSON.stringify({ columns, domains, min_score: minScore });
      return JSON.parse(glue.detect_domain_json(input)) as DetectionResult;
    },
  };
}
```

- [ ] **Step 3: `index.ts`** (mirror `goldenanalysis/src/core/wasm/index.ts`):
```ts
/**
 * Public opt-in WASM API for infermap detect. enableInfermapWasm() is async;
 * after it resolves, the sync detectDomain* runs against the instantiated module.
 * Pure-TS stays the default + fallback. Plumbing lives in goldenmatch-wasm-runtime.
 */
import { enableWasmBackend, type EnableOptions } from "goldenmatch-wasm-runtime";
import { setInfermapBackend } from "./backend.js";

export type { InfermapBackend } from "./backend.js";
export type EnableInfermapWasmOptions = EnableOptions;

let _enabled = false;

export async function enableInfermapWasm(
  opts: EnableInfermapWasmOptions = {},
): Promise<boolean> {
  if (_enabled) return true;
  try {
    const { instantiateBackend } = await import("./loader.js");
    const ok = await enableWasmBackend(
      opts,
      instantiateBackend,
      setInfermapBackend,
      new URL("./artifacts/infermap_wasm_bg.wasm", import.meta.url),
    );
    if (ok) _enabled = true;
    return ok;
  } catch (err) {
    if (opts.require) throw err;
    return false;
  }
}

export function disableInfermapWasm(): void {
  setInfermapBackend(null);
  _enabled = false;
}
```

- [ ] **Step 4: Eye-verify** against `goldenanalysis/src/core/wasm/{backend,loader,index}.ts`: the `createBackendRegistry`/`enableWasmBackend` arg order + `glue.default({module_or_path})` shape + the `new URL("./artifacts/infermap_wasm_bg.wasm", import.meta.url)` line must match (with `infermap`/`detect` substituted). Confirm `.js` extensions on all relative imports (ESM/nodenext).

- [ ] **Step 5: Commit:**
```bash
git add packages/typescript/infermap/src/core/wasm/backend.ts packages/typescript/infermap/src/core/wasm/loader.ts packages/typescript/infermap/src/core/wasm/index.ts
git commit -m "feat(infermap-ts): wasm backend/loader/index scaffold (Wave A)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

**Report:** `DONE`/`BLOCKED`, SHA.

---

## Task 4: `detect.ts` hoist refactor + `scoreDomains` + backend dispatch

**Files:** Modify `packages/typescript/infermap/src/core/detect.ts`.
**Box:** eye-review only. The existing `tests/domainPack.test.ts` (which tests `detectDomain`) must stay green — verified in CI.

**Context:** Today `detectDomainDetailed` FUSES hint-resolution into the scoring loop (`domains` is a `string[]` of names; `loadDomain`+`allHints` Set built inline per-name). This task (a) hoists resolution into a pre-pass producing `resolved: Array<[string,string[]]>`, (b) extracts the pure scoring into an exported `scoreDomains(columns, resolved, minScore)` (byte-identical to the old inline scoring, for the parity gate), and (c) dispatches to the backend when set. Public API unchanged.

- [ ] **Step 1: Add the import** near the top of `detect.ts` (after the existing `goldencheck-types` imports):
```ts
import { getInfermapBackend } from "./wasm/backend.js";
```

- [ ] **Step 2: Add the exported `scoreDomains`** (place it above `detectDomainDetailed`). This is the current inline scoring, verbatim, re-expressed over a resolved list:
```ts
/** Pure scoring over resolved [name, hints[]] domains — the WASM parity oracle.
 *  Byte-identical to infermap-core::detect_domain for NON-EMPTY columns. The
 *  empty-columns `no_data` guard lives in detectDomainDetailed (the kernel guards
 *  it too), so scoreDomains is never called with empty columns in production. */
export function scoreDomains(
  columns: string[],
  resolved: Array<[string, string[]]>,
  minScore: number,
): DetectionResult {
  const scored: Array<[string, number]> = [];
  for (const [name, hints] of resolved) {
    if (hints.length === 0) continue; // == Rust `hints.is_empty()` skip
    let hits = 0;
    for (const c of columns) {
      for (const h of hints) {
        if (hintMatches(h, c)) {
          hits++;
          break;
        }
      }
    }
    scored.push([name, hits / Math.max(columns.length, 1)]);
  }
  if (scored.length === 0) {
    return { domain: null, score: 0, runner_up: null, runner_up_score: 0, reason: "no_data" };
  }
  scored.sort((a, b) => b[1] - a[1]);
  const [bestName, bestScore] = scored[0]!;
  const runner = scored[1];
  const runnerName = runner ? runner[0] : null;
  const runnerScore = runner ? runner[1] : 0;
  if (bestScore < minScore) {
    return { domain: null, score: bestScore, runner_up: runnerName, runner_up_score: runnerScore, reason: "below_min_score" };
  }
  const topCount = scored.filter(([, s]) => s === bestScore).length;
  if (topCount > 1) {
    return { domain: null, score: bestScore, runner_up: runnerName, runner_up_score: runnerScore, reason: "tie" };
  }
  return { domain: bestName, score: bestScore, runner_up: runnerName, runner_up_score: runnerScore, reason: "confident" };
}
```

- [ ] **Step 3: Rewrite the scoring region of `detectDomainDetailed`.** Keep the column-resolution + BOTH `no_data` early guards (no columns / empty columns) exactly as they are. Replace everything from `const domains = candidates ?? ...` through the final `return {... "confident"}` with the resolve pre-pass + dispatch:
```ts
  const domainNames = candidates ?? listDomains().filter((d) => d !== "generic");
  // Hoist hint resolution into a pre-pass so the SAME resolved input feeds the
  // kernel or the pure path. Empty-hint domains are INCLUDED (both paths skip
  // them: kernel via hints.is_empty(), pure via hints.length===0), so scoring is
  // identical to the pre-refactor inline loop.
  const resolved: Array<[string, string[]]> = domainNames.map((d) => {
    const pack = loadDomain(d);
    const allHints = new Set<string>();
    for (const spec of Object.values(pack.types)) {
      for (const h of spec.name_hints) allHints.add(h);
    }
    return [d, Array.from(allHints)];
  });

  const backend = getInfermapBackend();
  if (backend) {
    return backend.detectDomain(columns, resolved, minScore);
  }
  return scoreDomains(columns, resolved, minScore);
```

- [ ] **Step 4: Eye-verify byte-identity.** Read the pre-refactor `detectDomainDetailed` (git: `git show HEAD:packages/typescript/infermap/src/core/detect.ts`). Confirm the pure path (resolve → `scoreDomains`) produces the SAME `scored` set and the SAME sort/tie/threshold tail: same domain iteration order (`domainNames` order == old `domains` order), empty-hint domains skipped identically, `hits / Math.max(columns.length, 1)` unchanged, `sort((a,b)=>b[1]-a[1])` + `filter(s===bestScore)` tail verbatim. Confirm `columns`/candidate resolution + the two `no_data` guards are untouched and still precede the resolve pre-pass.

- [ ] **Step 5: Commit:**
```bash
git add packages/typescript/infermap/src/core/detect.ts
git commit -m "refactor(infermap-ts): hoist detect hint-resolution + scoreDomains + wasm dispatch (Wave A)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

**Report:** `DONE`/`BLOCKED`, SHA, and a one-line confirmation the pure path is byte-identical to pre-refactor.

---

## Task 5: `package.json` devDep + `tsup.config.ts` artifact keys

**Files:** Modify `packages/typescript/infermap/package.json`, `.../tsup.config.ts`.
**Box:** JSON/eye-review. Confirm against `goldenanalysis/{package.json,tsup.config.ts}`.

- [ ] **Step 1: Add the devDependency.** In `packages/typescript/infermap/package.json`, add to **`devDependencies`** (NOT `dependencies` — it's inlined by tsup, never published):
```json
    "goldenmatch-wasm-runtime": "workspace:^",
```
(Match the existing `devDependencies` block's formatting/ordering. If there's no `devDependencies` block, add one.)

- [ ] **Step 2: Update `tsup.config.ts`.** Replace `dts: true` with the resolve form and add the artifact-shipping keys (mirror `goldenanalysis/tsup.config.ts`):
```ts
  // resolve: roll the bundled goldenmatch-wasm-runtime types INTO our .d.ts
  // (noExternal inlines the JS, but tsup keeps a bare import in the dts otherwise).
  dts: { resolve: ["goldenmatch-wasm-runtime"] },
  // Copy the opt-in WASM artifact into dist so the loader's
  // new URL('./artifacts/infermap_wasm_bg.wasm', import.meta.url) resolves at
  // runtime. Absent in a default checkout -> enableInfermapWasm() returns false.
  loader: { ".wasm": "copy" },
  publicDir: false,
  onSuccess: "node scripts/copy_wasm_artifact.mjs",
  // Inline the tiny WASM plumbing so it's not a published runtime dep.
  noExternal: ["goldenmatch-wasm-runtime"],
  external: [
    // Runtime-only wasm-bindgen glue (dynamic-imported in enableInfermapWasm);
    // absent in a default checkout. Mark external so esbuild never resolves it.
    /infermap_wasm\.js$/,
  ],
```
Keep the existing `entry`, `format`, `sourcemap`, `clean`, `target`, `splitting`, `treeshake` keys. (The `node/a2a/server` entry, if present from an earlier PR, stays.)

- [ ] **Step 3: Verify.** `python -c "import json; json.load(open('packages/typescript/infermap/package.json')); print('package.json OK')"`. Eye-compare the tsup keys against `goldenanalysis/tsup.config.ts` (only the `analysis_wasm`→`infermap_wasm` substring differs). Confirm `dts: true` is gone.

- [ ] **Step 4: Commit:**
```bash
git add packages/typescript/infermap/package.json packages/typescript/infermap/tsup.config.ts
git commit -m "build(infermap-ts): wasm-runtime devDep + tsup artifact-shipping keys (Wave A)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

**Report:** `DONE`/`BLOCKED`, SHA, package.json-OK.

> **Lockfile note for the controller:** adding a workspace devDep changes
> `pnpm-lock.yaml`. The box may not be able to run `pnpm install` (OOM). If the CI
> `typescript`/`infermap_wasm` lane fails on a frozen-lockfile mismatch, the
> controller regenerates `pnpm-lock.yaml` in a later step (or CI does). Flag, don't block.

---

## Task 6: `copy_wasm_artifact.mjs`

**Files:** Create `packages/typescript/infermap/scripts/copy_wasm_artifact.mjs`.
**Box:** `node --check` (this IS box-runnable — it's an `.mjs`).

- [ ] **Step 1: Create the script** (mirror `goldenanalysis/scripts/copy_wasm_artifact.mjs`, `analysis`→`infermap`):
```js
// Copy the built WASM artifact from src into the dist locations the bundled
// loader might resolve `new URL('./artifacts/infermap_wasm_bg.wasm', import.meta.url)`
// to. tsup bundling can land the loader at several depths; copy to every plausible
// `./artifacts/` parent (a few KB, harmless). No-op (warns) when the artifact is absent.
import { cp, mkdir, access } from "node:fs/promises";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const src = join(here, "..", "src", "core", "wasm", "artifacts");
const files = ["infermap_wasm_bg.wasm", "infermap_wasm.js"];
const dsts = [
  join(here, "..", "dist", "core", "wasm", "artifacts"),
  join(here, "..", "dist", "core", "artifacts"),
  join(here, "..", "dist", "artifacts"),
];

try {
  await access(join(src, files[0]));
} catch {
  console.warn("[copy_wasm_artifact] no WASM artifact in src — skipping (pure-TS default).");
  process.exit(0);
}
for (const dst of dsts) {
  await mkdir(dst, { recursive: true });
  for (const f of files) await cp(join(src, f), join(dst, f));
}
console.log("[copy_wasm_artifact] copied", files.join(", "), "to", dsts.length, "dist locations");
```

- [ ] **Step 2: Verify:** `node --check packages/typescript/infermap/scripts/copy_wasm_artifact.mjs` → no output = OK. Eye-compare against the analysis script (`analysis_wasm`→`infermap_wasm` in `files`).

- [ ] **Step 3: Commit:**
```bash
git add packages/typescript/infermap/scripts/copy_wasm_artifact.mjs
git commit -m "build(infermap-ts): copy_wasm_artifact.mjs dist fan-out (Wave A)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

**Report:** `DONE`/`BLOCKED`, SHA, `node --check` result.

---

## Task 7: Parity gate — `infermap-wasm.parity.test.ts`

**Files:** Create `packages/typescript/infermap/tests/parity/infermap-wasm.parity.test.ts`.
**Box:** eye-review (no vitest). Runs un-skipped only in CI (artifact present).

**Design:** compares the WASM backend (`getInfermapBackend().detectDomain`, the Rust kernel) against the pure `scoreDomains` over a SYNTHETIC domain corpus with NON-EMPTY columns — the 8 Wave-1 Python parity cases minus the empty-columns case (excluded because the kernel guards empty→no_data while `scoreDomains` assumes the caller guarded it; they are equivalent only for non-empty columns, which is all production ever feeds them). This is the truest drift audit: identical synthetic inputs to both engines.

- [ ] **Step 1: Create the test:**
```ts
/**
 * WASM-vs-pure-TS parity for infermap `detect`. The WASM backend wraps
 * infermap-core::detect_domain (== Python == the Rust FFI); scoreDomains is the
 * pure-TS reimplementation. This gate asserts they agree byte-for-byte over a
 * synthetic domain corpus (the Wave-1 kernel-parity cases, non-empty columns).
 *
 * Skipped when the built artifact is absent (default checkout / no toolchain);
 * the CI `infermap_wasm` lane builds it first and runs this un-skipped. Any
 * DISAGREEMENT is a real TS-vs-Rust `detect` drift finding (hintMatches token
 * logic, tie order) — WASM is the reference; surface it, don't skip it.
 */
import { describe, it, expect, afterAll } from "vitest";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { scoreDomains } from "../../src/core/detect.js";
import {
  enableInfermapWasm,
  disableInfermapWasm,
} from "../../src/core/wasm/index.js";
import { getInfermapBackend } from "../../src/core/wasm/backend.js";

const artifact = fileURLToPath(
  new URL("../../src/core/wasm/artifacts/infermap_wasm_bg.wasm", import.meta.url),
);
const d = existsSync(artifact) ? describe : describe.skip;

// (columns, domains [name,hints[]][], minScore) — non-empty columns only.
// Mirrors infermap Python test_native_parity._CASES (minus the empty-columns case).
type Case = [string[], Array<[string, string[]]>, number];
const CASES: Case[] = [
  [["provider_npi", "first_name"], [["health", ["provider npi"]], ["fin", ["iban"]]], 0.3], // confident
  [["a", "b"], [["x", ["a"]], ["y", ["b"]]], 0.3], // 2-way tie
  [["a", "b"], [["x", ["a"]], ["y", ["b"]], ["z", ["a"]]], 0.3], // 3-way tie (host order)
  [["a", "b", "c", "d"], [["h", ["a"]]], 0.3], // below_min_score (0.25)
  [["a"], [["h", []]], 0.3], // no_data (all hint-less)
  [["patient_id", "provider_npi", "dob"], [["health", ["patient id", "npi"]], ["fin", ["iban"]]], 0.3],
  [["a"], [["h", ["a b c"]]], 0.3], // hint longer than column
  [["ORDER_ID", "Sku"], [["ecom", ["order id", "sku"]]], 0.3], // ASCII case-insensitivity
];

d("infermap detect WASM-vs-pure parity", () => {
  afterAll(() => disableInfermapWasm());

  it("enableInfermapWasm() succeeds in this lane (artifact present)", async () => {
    disableInfermapWasm();
    const ok = await enableInfermapWasm({ require: true });
    expect(ok).toBe(true);
    disableInfermapWasm();
  });

  for (let i = 0; i < CASES.length; i++) {
    const [columns, domains, minScore] = CASES[i]!;
    it(`case ${i}: kernel == scoreDomains`, async () => {
      const pure = scoreDomains(columns, domains, minScore);
      const ok = await enableInfermapWasm({ require: true });
      expect(ok).toBe(true);
      const backend = getInfermapBackend()!;
      const wasm = backend.detectDomain(columns, domains, minScore);
      disableInfermapWasm();
      expect(wasm).toEqual(pure); // deep-equal DetectionResult; drift => fail
    });
  }
});
```

- [ ] **Step 2: Eye-verify.** Confirm the `existsSync(artifact) ? describe : describe.skip` skip pattern matches `goldenanalysis/tests/parity/wasm-aggregate.test.ts`. Confirm imports resolve to real exports: `scoreDomains` from `detect.ts` (Task 4), `enable/disableInfermapWasm` from `wasm/index.ts`, `getInfermapBackend` from `wasm/backend.ts`. Confirm NO empty-columns case (would spuriously fail: kernel no_data vs scoreDomains below_min).

- [ ] **Step 3: Commit:**
```bash
git add packages/typescript/infermap/tests/parity/infermap-wasm.parity.test.ts
git commit -m "test(infermap-ts): WASM-vs-pure detect parity gate (Wave A)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

**Report:** `DONE`/`BLOCKED`, SHA.

---

## Task 8: CI `infermap_wasm` lane + paths-filter

**Files:** Modify `.github/workflows/ci.yml`.
**Box:** YAML-validate.

- [ ] **Step 1: Add the paths-filter output** in the `changes` job's `outputs:` block (near the `analysis_wasm:` output line, ~line 87):
```yaml
      infermap_wasm: ${{ steps.filter.outputs.infermap_wasm }}
```

- [ ] **Step 2: Add the filter entry** in the `dorny/paths-filter` `filters:` block (after the `analysis_wasm:` filter, ~line 234), mirroring it:
```yaml
            infermap_wasm:
              - 'packages/rust/extensions/infermap-wasm/**'
              - 'packages/rust/extensions/infermap-core/**'
              - 'packages/typescript/goldenmatch-wasm-runtime/**'
              - 'packages/typescript/infermap/src/core/wasm/**'
              - 'packages/typescript/infermap/src/core/detect.ts'
              - 'packages/typescript/infermap/tests/parity/infermap-wasm.parity.test.ts'
              - 'packages/typescript/infermap/scripts/copy_wasm_artifact.mjs'
              - 'packages/typescript/infermap/tsup.config.ts'
```

- [ ] **Step 3: Add the job** after the `analysis_wasm:` job (~line 1934), mirroring it step-for-step:
```yaml
  infermap_wasm:
    needs: changes
    if: needs.changes.outputs.infermap_wasm == 'true' || needs.changes.outputs.force_all == 'true'
    # Opt-in WASM detect lane: builds the infermap-wasm artifact, then runs the
    # detect parity test UN-skipped (skips elsewhere with no artifact). Advisory.
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@df4cb1c069e1874edd31b4311f1884172cec0e10  # v6
      - uses: dtolnay/rust-toolchain@29eef336d9b2848a0b548edc03f92a220660cdb8  # stable
        with:
          targets: wasm32-unknown-unknown
      - uses: Swatinem/rust-cache@e18b497796c12c097a38f9edb9d0641fb99eee32  # v2
        with:
          workspaces: packages/rust/extensions/infermap-wasm
      - uses: pnpm/action-setup@0ebf47130e4866e96fce0953f49152a61190b271  # v6.0.9
      - uses: actions/setup-node@48b55a011bda9f5d6aeb4c2d9c7362e8dae4041e  # v6.4.0
        with:
          node-version: 22
          cache: pnpm
      - run: pnpm install --frozen-lockfile
      - name: Build WASM artifact (installs the matching wasm-bindgen-cli)
        run: bash packages/rust/extensions/infermap-wasm/build_wasm.sh
      - name: Build shared wasm-runtime (the parity test imports goldenmatch-wasm-runtime)
        run: pnpm --filter goldenmatch-wasm-runtime build
      - name: WASM detect parity (un-skipped — artifact now present) [THE GATE]
        run: pnpm --filter infermap exec vitest run tests/parity/infermap-wasm.parity.test.ts
      - name: dist-path validation (enableInfermapWasm() must resolve the bundled artifact)
        run: |
          pnpm --filter infermap build
```

- [ ] **Step 4: Validate YAML + eye-check** (a broken ci.yml = zero jobs = required gate never reports):
```bash
"D:/show_case/goldenmatch/.venv/Scripts/python.exe" -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml')); print('ci.yml YAML OK')"
grep -n "infermap_wasm" .github/workflows/ci.yml
```
Expect: `ci.yml YAML OK`; the output line, the filter block, and the job all present. **Confirm the `pnpm --filter infermap` name matches `packages/typescript/infermap/package.json`'s `"name"` (it's `infermap`).**

- [ ] **Step 5: Commit:**
```bash
git add .github/workflows/ci.yml
git commit -m "ci(infermap): infermap_wasm advisory lane + paths-filter (Wave A)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

**Report:** `DONE`/`BLOCKED`, SHA, YAML-OK + grep output.

---

## Task 9: Rebase + push + PR + arm auto-merge (controller runs this)

**Files:** none.

- [ ] **Step 1: Rebase onto fresh origin/main** (main moves fast):
```bash
unset GH_TOKEN; gh auth switch --user benzsevern; export GH_TOKEN=$(gh auth token --user benzsevern)
git fetch origin main -q
git rebase origin/main
```
Conflicts unlikely (all-new files + isolated `detect.ts`/`tsup.config.ts`/`package.json`/`ci.yml` regions). If `ci.yml` conflicts against another lane addition, keep both. Re-validate YAML after any resolution.

- [ ] **Step 2: Confirm the three-dot diff is clean:**
```bash
git diff --stat origin/main...HEAD
```
Expect only the Wave A files (spec, plan, the crate, the TS wasm module + detect.ts + package.json + tsup + copy script + parity test + ci.yml). If unrelated files appear, STOP.

- [ ] **Step 3: Push:**
```bash
git push -u origin feat/infermap-wasm-wave-a
```

- [ ] **Step 4: Open the PR:**
```bash
gh pr create --repo benseverndev-oss/goldenmatch --base main \
  --title "feat(infermap): WASM/TS Wave A — infermap-wasm foundation + detect_domain" \
  --body "$(cat <<'EOF'
## What

Wave A of the InferMap WASM/TS surface: a new `infermap-wasm` wasm-bindgen crate over `infermap-core`, the TS `wasm/{backend,loader,index}.ts` plumbing, a build script, a CI lane, and a byte-parity gate — with `detect_domain` wired through end to end.

Makes the TS `infermap` surface run the SAME `infermap-core` kernel Python's native wheel runs, instead of its separate hand-written reimplementation (anti-drift).

## How

- **Crate**: `detect_domain_json(input_json) -> output_json` — JSON boundary crossed once per call; serde DTOs in the wrapper, `infermap-core` stays serde-free.
- **TS**: `detect.ts` hoists hint-resolution into a pre-pass, then dispatches to the WASM backend (or the pure `scoreDomains` fallback). Public API unchanged; the pure path is byte-identical to before.
- **Artifact**: CI-built, not committed (`describe.skip` locally; the `infermap_wasm` lane builds it then runs parity un-skipped). Build = `cargo build` + pinned `wasm-bindgen-cli`, mirroring `score-wasm/build_wasm.sh`.

## Parity = drift audit

`infermap-wasm.parity.test.ts` asserts `kernel.detectDomain` deep-equals pure `scoreDomains` over the 8 Wave-1 synthetic detect cases (non-empty columns). Any disagreement is a real TS-vs-Rust `detect` divergence (hintMatches token logic / tie order) — WASM is the reference; surfaced here, not papered over. **If CI reddens on a parity case, that is the audit working** — the PR notes the divergence + resolution.

## Scope

Foundation + one kernel. The other 5 kernels are Waves B/C.

Spec: `docs/superpowers/specs/2026-07-06-infermap-wasm-wave-a-design.md`
Plan: `docs/superpowers/plans/2026-07-06-infermap-wasm-wave-a.md`

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44
EOF
)"
```

- [ ] **Step 5: Arm auto-merge + STOP:**
```bash
gh pr merge <PR#> --repo benseverndev-oss/goldenmatch --squash --auto
```
Do NOT `--delete-branch`. Do NOT poll CI. **BUT:** this lane's parity test is a drift audit that may legitimately red. After arming, do ONE check of the `infermap_wasm` lane result: if it fails on a parity assertion, that is a real finding to investigate + report (not a flaky infra fail) — the WASM/Rust detect and the pure-TS detect genuinely diverge on that input, and the resolution is to make WASM the reference and document/fix the pure path in a follow-up. Report the PR number + the drift status.

---

## Verification Summary

| What | How | Where |
| --- | --- | --- |
| Crate compiles + boundary round-trips | Rust `#[cfg(test)]` host unit tests | CI (Task 1) |
| wasm builds + glue emitted | `build_wasm.sh` in the lane | CI (Task 8) |
| TS wasm module typechecks | tsc/tsup in `typescript` + `infermap_wasm` lanes | CI (Tasks 3,5) |
| detect pure path byte-identical | existing `domainPack.test.ts` stays green | CI (Task 4) |
| **WASM kernel == pure scoreDomains** | `infermap-wasm.parity.test.ts` (8 cases, deep-equal) | CI `infermap_wasm` lane (Task 7) — **drift audit** |
| copy script valid | `node --check` | Box (Task 6) |
| ci.yml valid | `yaml.safe_load` | Box (Task 8) |
| No unrelated diff | three-dot diff | Box (Task 9) |
