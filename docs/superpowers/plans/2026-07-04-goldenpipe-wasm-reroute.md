# GoldenPipe SP3 — WASM Reroute + Drift-Kill Parity Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give edge-TS a `goldenpipe-wasm` kernel (goldenpipe-core compiled to wasm), reroute the five TS planner seams through it under an opt-in `enableWasm()`, conform pure-TS `autoConfig` to the core, and add a cross-surface parity gate (pure-TS == wasm == core) that locks the TS planner to the single Rust reference forever.

**Architecture:** Mirror goldenflow's Wave 0a/0c pattern exactly. A pyo3-free wasm-bindgen crate wraps `goldenpipe-core::json` (5 string→string fns). The TS package gains `src/core/wasm/{backend,loader,index}.ts` (opt-in backend singleton + `enableWasm`), a `plannerJsonPure.ts` shim (the Leg A serializer, TS analogue of SP2's `_planner_json.py`), and a `plannerJson.ts` deserialize side that the five planner functions call when a backend is registered. Pure-TS is the default and permanent edge fallback. The parity gate reuses the SP1 golden vectors as shared truth.

**Tech Stack:** Rust + wasm-bindgen (`goldenpipe-wasm`), `goldenpipe-core` (path dep, MERGED), TypeScript + vitest, `goldenmatch-wasm-runtime` (shared byte loader), GitHub Actions + `dorny/paths-filter`.

**Box constraints (READ FIRST):**
- **Rust is box-safe.** Host `cargo build`/`cargo test`/`fmt`/`clippy` run locally on NTFS `D:` (toolchain 1.94.0, `CARGO_HOME=D:\.cargo`, PATH prepend `D:\.rustup\toolchains\1.94.0-*\bin`). The `cfg(target_arch="wasm32")` gate keeps the host build empty, so host `cargo test` compiles clean WITHOUT a wasm target. The wasm32 artifact build happens in CI.
- **TS is CI-ONLY.** `pnpm`/`tsc`/`vitest` OOM-kill the box (exit 137). Do NOT run vitest or turbo locally. TS source + tests are written on-box but VERIFIED IN CI — push and read the `goldenpipe_wasm` lane. Each TS task's "run the test" step means "CI runs it on push"; locally, only static review.
- `benzsevern` gh account (unset `GH_TOKEN` if it overrides `gh auth switch`). After the PR is up, arm `gh pr merge --auto --squash` and STOP — do not poll CI.
- Golden vectors are already on `main` at `packages/rust/extensions/goldenpipe-core/tests/vectors/{resolve,apply_decision,evaluate_builtin,auto_config,skip_if}.json`.

**Deviation from spec §3.6 (M5), flagged:** the spec said Leg B passes `{ wasmBase64 }`. The proven goldenflow `wasm_flow` CI gate instead builds `_bg.wasm` into `src/core/wasm/artifacts/` and calls `enableWasm()` (URL/fs default). This plan follows the proven mirror for the CI gate (lower risk, identical shape) and keeps base64 as an available opt-in edge strategy via `enableWasm({ wasmBase64 })` (the shared runtime already supports it, `goldenmatch-wasm-runtime/src/index.ts:70-74`). No functional loss: same bytes, same kernel.

---

### Task 1: `goldenpipe-wasm` crate

**Files:**
- Create: `packages/rust/extensions/goldenpipe-wasm/Cargo.toml`
- Create: `packages/rust/extensions/goldenpipe-wasm/src/lib.rs`
- Reference (mirror): `packages/rust/extensions/goldenflow-wasm/{Cargo.toml,src/lib.rs}`; kernel `packages/rust/extensions/goldenpipe-core/src/json.rs`

- [ ] **Step 1: Write the crate `Cargo.toml`**

```toml
# Standalone workspace so this wasm-bindgen wrapper can path-depend on the
# pyo3-free `goldenpipe-core` WITHOUT either crate's workspace claiming it —
# same isolation rationale as goldenflow-wasm / score-wasm. NO rust-toolchain.toml
# (inherits the caller's toolchain). Byte-identical to Python/native by
# construction: it wraps the SAME goldenpipe-core::json kernels.
[workspace]

[package]
name = "goldenpipe-wasm"
version = "0.1.0"
edition = "2021"
license = "MIT"
authors = ["Ben Severn <benzsevern@gmail.com>"]
description = "wasm-bindgen wrapper over goldenpipe-core planner kernels for the GoldenPipe TS opt-in WASM backend"

[lib]
crate-type = ["cdylib", "rlib"]  # cdylib for wasm; rlib so host unit tests link

[dependencies]
goldenpipe-core = { path = "../goldenpipe-core" }

[target.'cfg(target_arch = "wasm32")'.dependencies]
wasm-bindgen = "0.2"
```

- [ ] **Step 2: Write `src/lib.rs`**

```rust
//! wasm-bindgen wrapper over `goldenpipe-core::json`. The TS analogue of the
//! goldenpipe-native pyo3 crate: thin shims delegating to `goldenpipe-core`
//! so the planner (resolve/route/decisions/auto_config/skip_if) is
//! byte-identical across Python, native, and TS WASM. All logic lives in
//! `goldenpipe-core` (the reference); this crate only marshals strings across
//! the JS<->WASM boundary.
//!
//! `wasm-bindgen` is a wasm32-only dependency (see Cargo.toml), so the actual
//! `#[wasm_bindgen]` exports live in a `cfg(target_arch = "wasm32")`-gated
//! module — this keeps a plain host `cargo build`/`cargo test` (no wasm
//! target) compiling clean, matching goldenflow-wasm / score-wasm.

#[cfg(target_arch = "wasm32")]
mod wasm {
    use goldenpipe_core::json;
    use wasm_bindgen::prelude::*;

    #[wasm_bindgen]
    pub fn resolve_json(input: &str) -> String {
        json::resolve_json(input)
    }

    #[wasm_bindgen]
    pub fn apply_decision_json(input: &str) -> String {
        json::apply_decision_json(input)
    }

    #[wasm_bindgen]
    pub fn evaluate_builtin_json(input: &str) -> String {
        json::evaluate_builtin_json(input)
    }

    #[wasm_bindgen]
    pub fn auto_config_json(input: &str) -> String {
        json::auto_config_json(input)
    }

    #[wasm_bindgen]
    pub fn skip_if_falsy_json(input: &str) -> String {
        json::skip_if_falsy_json(input)
    }
}
```

- [ ] **Step 3: Verify host build + lint are clean**

Run (with the box Rust env — `CARGO_HOME=D:\.cargo`, PATH prepended):
```bash
cargo build --manifest-path packages/rust/extensions/goldenpipe-wasm/Cargo.toml
cargo test  --manifest-path packages/rust/extensions/goldenpipe-wasm/Cargo.toml
cargo fmt   --manifest-path packages/rust/extensions/goldenpipe-wasm/Cargo.toml -- --check
cargo clippy --manifest-path packages/rust/extensions/goldenpipe-wasm/Cargo.toml -- -D warnings
```
Expected: all pass. The `cfg(wasm32)` gate means the host build is an empty lib (no wasm-bindgen linked) — this is intentional and matches goldenflow-wasm. Zero warnings.

- [ ] **Step 4: (optional, if `wasm32-unknown-unknown` target is installed locally) confirm the wasm target compiles**

```bash
cargo build --manifest-path packages/rust/extensions/goldenpipe-wasm/Cargo.toml --target wasm32-unknown-unknown --release
```
Expected: builds `goldenpipe_wasm.wasm`. If the target isn't installed, SKIP — CI does this in Task 7. (SP1 already verified `goldenpipe-core` builds for `wasm32-unknown-unknown`.)

- [ ] **Step 5: Commit**

```bash
git add packages/rust/extensions/goldenpipe-wasm
git commit -m "feat(goldenpipe-wasm): wasm-bindgen crate over goldenpipe-core planner kernels"
```

---

### Task 2: TS opt-in backend + loader + `enableWasm`

**Files:**
- Create: `packages/typescript/goldenpipe/src/core/wasm/backend.ts`
- Create: `packages/typescript/goldenpipe/src/core/wasm/loader.ts`
- Create: `packages/typescript/goldenpipe/src/core/wasm/index.ts`
- Test: `packages/typescript/goldenpipe/tests/unit/wasm-backend.test.ts`
- Reference (mirror): `packages/typescript/goldenflow/src/core/wasm/{backend,loader,index}.ts`

- [ ] **Step 1: Write the failing backend-registry unit test** (CI-verified)

```ts
import { describe, it, expect, afterEach } from "vitest";
import {
  setPipeWasmBackend,
  getPipeWasmBackend,
  type PipeWasmBackend,
} from "../../src/core/wasm/backend.js";

const fake: PipeWasmBackend = {
  resolveJson: () => "{}",
  applyDecisionJson: () => "{}",
  evaluateBuiltinJson: () => "null",
  autoConfigJson: () => "{}",
  skipIfFalsyJson: () => "true",
};

describe("PipeWasmBackend registry", () => {
  afterEach(() => setPipeWasmBackend(null));

  it("is null by default", () => {
    expect(getPipeWasmBackend()).toBeNull();
  });
  it("set/get round-trips and reset isolates", () => {
    setPipeWasmBackend(fake);
    expect(getPipeWasmBackend()).toBe(fake);
    setPipeWasmBackend(null);
    expect(getPipeWasmBackend()).toBeNull();
  });
});
```

- [ ] **Step 2: Write `backend.ts`**

```ts
/**
 * backend.ts — opt-in WASM planner-kernel backend registry. Edge-safe: no
 * node:* here. The active backend (if any) is consulted by the five planner
 * seams (Resolver.resolve, Router.apply, the decision gates, autoConfig, the
 * runner's isFalsy) via getPipeWasmBackend(); everything else stays pure-TS.
 * Mirrors goldenflow's setFlowWasmBackend module-singleton for test isolation.
 */

/**
 * A WASM-backed planner kernel over goldenpipe-core's five JSON wrappers
 * (see goldenpipe-wasm/src/lib.rs). Byte-identical to the Python/native
 * kernels by construction — a thin wasm-bindgen shim over the SAME
 * goldenpipe-core::json module. All five are string -> string.
 */
export interface PipeWasmBackend {
  resolveJson(input: string): string;
  applyDecisionJson(input: string): string;
  evaluateBuiltinJson(input: string): string;
  autoConfigJson(input: string): string;
  skipIfFalsyJson(input: string): string;
}

import { createBackendRegistry } from "goldenmatch-wasm-runtime";

const _registry = createBackendRegistry<PipeWasmBackend>();

export function setPipeWasmBackend(b: PipeWasmBackend | null): void {
  _registry.set(b);
}

export function getPipeWasmBackend(): PipeWasmBackend | null {
  return _registry.get();
}
```

- [ ] **Step 3: Write `loader.ts`** (mirror goldenflow; adapt the 5 snake_case exports)

```ts
/**
 * loader.ts — universal WASM byte loader + instantiation for the goldenpipe
 * planner kernel. Edge-safe: the only node:* touch is inside the shared
 * runtime's resolveWasmBytes. Resolution order (delegated): explicit bytes ->
 * base64 -> URL -> fs (Node) -> fetch. Any failure throws; index.ts turns
 * that into the pure-TS fallback (or rethrows under { require: true }).
 */
import {
  resolveWasmBytes as sharedResolveWasmBytes,
  type LoadOptions,
} from "goldenmatch-wasm-runtime";
import type { PipeWasmBackend } from "./backend.js";

export type { LoadOptions };

export function resolveWasmBytes(opts: LoadOptions): Promise<Uint8Array> {
  return sharedResolveWasmBytes(
    opts,
    new URL("./artifacts/goldenpipe_wasm_bg.wasm", import.meta.url),
  );
}

export async function instantiateBackend(bytes: Uint8Array): Promise<PipeWasmBackend> {
  const glue = (await import("./artifacts/goldenpipe_wasm.js" as string)) as {
    default: (input: { module_or_path: Uint8Array }) => Promise<unknown>;
    resolve_json: (s: string) => string;
    apply_decision_json: (s: string) => string;
    evaluate_builtin_json: (s: string) => string;
    auto_config_json: (s: string) => string;
    skip_if_falsy_json: (s: string) => string;
  };
  await glue.default({ module_or_path: bytes });

  return {
    resolveJson: (s) => glue.resolve_json(s),
    applyDecisionJson: (s) => glue.apply_decision_json(s),
    evaluateBuiltinJson: (s) => glue.evaluate_builtin_json(s),
    autoConfigJson: (s) => glue.auto_config_json(s),
    skipIfFalsyJson: (s) => glue.skip_if_falsy_json(s),
  };
}
```

- [ ] **Step 4: Write `index.ts`** (mirror goldenflow's enableWasm/isWasmEnabled/disableWasm verbatim, swapping names)

```ts
/**
 * Public opt-in WASM API for the goldenpipe planner kernel. enableWasm() is
 * async; after it resolves, the five planner seams consult the registered
 * backend instead of pure-TS. Pure-TS stays the default + fallback.
 */
import { enableWasmBackend, type EnableOptions } from "goldenmatch-wasm-runtime";
import { setPipeWasmBackend, getPipeWasmBackend } from "./backend.js";

export type { PipeWasmBackend } from "./backend.js";
export type EnableWasmOptions = EnableOptions;

let _enabled = false;

export async function enableWasm(opts: EnableWasmOptions = {}): Promise<boolean> {
  if (_enabled) return true;
  try {
    const { instantiateBackend } = await import("./loader.js");
    const ok = await enableWasmBackend(
      opts,
      instantiateBackend,
      setPipeWasmBackend,
      new URL("./artifacts/goldenpipe_wasm_bg.wasm", import.meta.url),
    );
    if (ok) _enabled = true;
    return ok;
  } catch (err) {
    if (opts.require) throw err;
    return false;
  }
}

export function isWasmEnabled(): boolean {
  return getPipeWasmBackend() !== null;
}

export function disableWasm(): void {
  setPipeWasmBackend(null);
  _enabled = false;
}
```

- [ ] **Step 5: Commit** (CI verifies the unit test on push)

```bash
git add packages/typescript/goldenpipe/src/core/wasm packages/typescript/goldenpipe/tests/unit/wasm-backend.test.ts
git commit -m "feat(goldenpipe): opt-in WASM planner backend + loader + enableWasm (pure-TS default)"
```

---

### Task 3: `autoConfig` identity conformance fix (pure-TS behavior change)

The substantive drift-kill. Today TS `autoConfig` has no identity path; the core appends the identity stage under `!identity_opts.is_empty() && registry.has(IDENTITY)`. This task threads an `identityOpts` option and replicates BOTH condition clauses so pure-TS == core (proven by the Leg A `auto_config` vectors in Task 4).

**Files:**
- Modify: `packages/typescript/goldenpipe/src/core/pipeline.ts:24-40,75-81`
- Reference: core condition `packages/rust/extensions/goldenpipe-core/src/config.rs:33-37`; Python `Pipeline(identity_opts=...)` + `_auto_config` in `packages/python/goldenpipe/goldenpipe/pipeline.py`; the identity stage's registry key (confirm it is `"goldenmatch.identity_resolve"` or the core's `IDENTITY` constant name — read `config.rs` for the exact string the core appends and match it EXACTLY).

- [ ] **Step 1: Read `config.rs:1-45` to capture the EXACT identity stage name + condition**

Run: read `packages/rust/extensions/goldenpipe-core/src/config.rs`. Note the literal identity stage `use` string the core appends and the `has(IDENTITY)` check. The `auto_config` golden vectors (`tests/vectors/auto_config.json`) encode the expected output — the fix must reproduce those byte-for-byte, including the `identity_unavailable_not_appended` case.

- [ ] **Step 2: Add `identityOpts` to `PipelineOptions` + store on the instance**

Modify `PipelineOptions` (pipeline.ts:26-29) and the constructor (pipeline.ts:35-40):
```ts
export interface PipelineOptions {
  config?: PipelineConfig | undefined;
  registry?: StageRegistry | undefined;
  identityOpts?: Record<string, unknown> | undefined;
}
```
```ts
  private readonly identityOpts: Record<string, unknown>;
  // ...in constructor:
  this.identityOpts = options?.identityOpts ?? {};
```

- [ ] **Step 3: Append the identity stage in `autoConfig` under the exact core condition**

Modify `autoConfig` (pipeline.ts:75-81). Use the identity stage name captured in Step 1 (shown here as `IDENTITY_STAGE` — replace with the literal from config.rs):
```ts
  private autoConfig(): PipelineConfig {
    const available = this.registry.listAll();
    const names = DEFAULT_STAGE_ORDER.filter((name) => name in available);
    // Conform to goldenpipe-core (config.rs:34): append identity iff identity
    // opts are non-empty AND the identity stage is registered.
    const IDENTITY_STAGE = "goldenmatch.identity_resolve"; // <- MATCH config.rs literal
    if (Object.keys(this.identityOpts).length > 0 && IDENTITY_STAGE in available) {
      names.push(IDENTITY_STAGE);
    }
    const stages = names.map((name) => makeStageSpec(name));
    return makePipelineConfig({ pipeline: "auto", stages });
  }
```

- [ ] **Step 4: (CI-verified via Task 4's Leg A `auto_config` vectors) — no separate test here**

The conformance is proven by the Leg A parity gate in Task 4 running the `auto_config` vectors through the pure-TS shim (which drives this `autoConfig`). Do not write a duplicate assertion.

- [ ] **Step 5: Commit**

```bash
git add packages/typescript/goldenpipe/src/core/pipeline.ts
git commit -m "feat(goldenpipe): conform pure-TS autoConfig to core identity path (identityOpts plumbing)"
```

---

### Task 4: `plannerJsonPure.ts` shim + Leg A parity gate (pure-TS == core)

The drift-lock. A pure-TS `*Json` shim (TS analogue of `_planner_json.py`) drives the REAL pure-TS planner and serializes to the core's JSON shapes; the Leg A test replays the SP1 vectors through it.

**Files:**
- Create: `packages/typescript/goldenpipe/src/core/wasm/plannerJsonPure.ts`
- Modify: `packages/typescript/goldenpipe/src/core/engine/runner.ts:17` (export `isFalsy`)
- Test: `packages/typescript/goldenpipe/tests/parity/planner-parity.test.ts`
- Reference (mirror closely): `packages/python/goldenpipe/goldenpipe/core/_planner_json.py`

- [ ] **Step 1: Export `isFalsy` from `runner.ts`**

Change `function isFalsy` (runner.ts:17) to `export function isFalsy`. The shim and the Task 5 reroute both consume the SAME predicate (so parity holds).

- [ ] **Step 2: Write the failing Leg A parity test**

```ts
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import {
  resolveJsonPure,
  applyDecisionJsonPure,
  evaluateBuiltinJsonPure,
  autoConfigJsonPure,
  skipIfFalsyJsonPure,
} from "../../src/core/wasm/plannerJsonPure.js";

// Vectors live in the goldenpipe-core crate (shared cross-surface truth).
const VEC = (name: string) =>
  fileURLToPath(
    new URL(
      `../../../../rust/extensions/goldenpipe-core/tests/vectors/${name}.json`,
      import.meta.url,
    ),
  );
const load = (name: string) => JSON.parse(readFileSync(VEC(name), "utf8")) as Array<{
  input: unknown;
  expected: unknown;
}>;

const FAMILIES: Array<[string, (s: string) => string]> = [
  ["resolve", resolveJsonPure],
  ["apply_decision", applyDecisionJsonPure],
  ["evaluate_builtin", evaluateBuiltinJsonPure],
  ["auto_config", autoConfigJsonPure],
  ["skip_if", skipIfFalsyJsonPure],
];

describe("Leg A — pure-TS planner == goldenpipe-core golden vectors", () => {
  for (const [name, fn] of FAMILIES) {
    it(`${name} vectors`, () => {
      for (const { input, expected } of load(name)) {
        expect(JSON.parse(fn(JSON.stringify(input)))).toEqual(expected);
      }
    });
  }
});
```
**Verify the relative vector path first:** from `packages/typescript/goldenpipe/tests/parity/` up to repo root is `../../../../` then `rust/extensions/...`. Confirm by counting: `tests/parity` -> `tests` -> `goldenpipe` -> `typescript` -> `packages`; that is 4 `..` to reach `packages/`, then `rust/extensions/...`. Adjust if the built test runs from `dist/` (vitest runs from source here — matches goldenflow's parity test).

- [ ] **Step 3: Run to verify it fails**

CI (or, if a targeted local vitest is unavoidable, it will OOM — rely on CI). Expected: FAIL, `plannerJsonPure` not found.

- [ ] **Step 4: Write `plannerJsonPure.ts`** (mirror `_planner_json.py` fn-for-fn)

Key adaptations from Python:
- **Stub registry keyed by `key`** (not `info.name`), so `key != name` resolve vectors work. `StageRegistry` keys by `info.name` via `register()` and has private fields, so build a duck-typed object implementing `has`/`get`/`listAll` and cast `as unknown as StageRegistry`.
- `Resolver.resolve` reads only `registry.has("load")`, `registry.get(use)`, and each stage's `.info` — so stub stages are `{ info: StageInfo }`.
- Serialize a PlannedStage as `{ name, use: p.spec.use, config: p.config, on_error: p.spec.onError }` plus `skip_if: p.spec.skipIf` when defined (matches core `PlannedSpec` + `_planned_to_dict`).
- Unknown-`use` → the pure-TS `registry.get` throws `Error("Stage '...' not found")`; catch it and emit `{ err: { kind: "unknown_stage", use: <first config use not in stub> } }` (mirror the Python KeyError branch).
- `WiringError` (resolver.ts) currently only carries `.message`. The shim must emit `{ err: { kind: "wiring", stage, missing, available } }`. **Two options — pick the additive one:** extend `WiringError` with optional `stage/missing/available` fields set at the throw site (mirrors SP2's additive `WiringError` change in `resolver.py`), and read them in the shim. Prefer this (keeps the message unchanged; existing consumers read `.message`). Include the additive `WiringError` edit in THIS task.

```ts
/**
 * plannerJsonPure.ts — the JSON face of the pure-TS planner. The Leg A parity
 * surface: each fn CALLS the real Resolver/Router/decisions/autoConfig/isFalsy
 * and serializes to goldenpipe-core's exact JSON shapes. Does NOT run at
 * pipeline runtime. TS analogue of goldenpipe/core/_planner_json.py.
 */
import { Resolver, WiringError, type PlannedStage } from "../engine/resolver.js";
import { Router } from "../engine/router.js";
import { isFalsy } from "../engine/runner.js";
import { severityGate, piiRouter, rowCountGate } from "../decisions.js";
import {
  makePipeContext,
  makePipelineConfig,
  makeStageSpec,
  makeDecision,
  type PipeContext,
  type StageInfo,
  type Stage,
  type StageSpec,
} from "../models.js";
import type { StageRegistry } from "../engine/registry.js";
import { Pipeline } from "../pipeline.js";

interface StubStage { info: StageInfo; }

class StubRegistry {
  private stages = new Map<string, StubStage>();
  add(key: string, info: StageInfo): void { this.stages.set(key, { info }); }
  has(key: string): boolean { return this.stages.has(key); }
  get(key: string): Stage {
    const s = this.stages.get(key);
    if (!s) throw new Error(`Stage '${key}' not found in registry`);
    return s as unknown as Stage;
  }
  listAll(): Record<string, StageInfo> {
    const out: Record<string, StageInfo> = {};
    for (const [k, s] of this.stages) out[k] = s.info;
    return out;
  }
  asRegistry(): StageRegistry { return this as unknown as StageRegistry; }
}

function info(d: { name: string; produces: string[]; consumes: string[] }): StageInfo {
  return { name: d.name, produces: [...d.produces], consumes: [...d.consumes] };
}

function plannedToDict(p: PlannedStage): Record<string, unknown> {
  const out: Record<string, unknown> = {
    name: p.name,
    use: p.spec.use,
    config: p.config ?? {},
    on_error: p.spec.onError,
  };
  if (p.spec.skipIf !== undefined && p.spec.skipIf !== null) out.skip_if = p.spec.skipIf;
  return out;
}

export function resolveJsonPure(inputStr: string): string {
  const arg = JSON.parse(inputStr) as {
    config: { pipeline: string; stages: unknown[]; decisions?: string[] };
    stages: Array<{ key: string; name: string; produces: string[]; consumes: string[] }>;
  };
  const reg = new StubRegistry();
  for (const s of arg.stages) reg.add(s.key, info(s));
  const config = makePipelineConfig(arg.config as never);
  try {
    const plan = Resolver.resolve(config, reg.asRegistry());
    return JSON.stringify({ ok: { stages: plan.stages.map(plannedToDict) } });
  } catch (e) {
    if (e instanceof WiringError) {
      return JSON.stringify({
        err: {
          kind: "wiring",
          // additive fields set at the throw site (see resolver.ts edit)
          stage: (e as WiringError & { stage?: string }).stage,
          missing: (e as WiringError & { missing?: string }).missing,
          available: (e as WiringError & { available?: string[] }).available,
        },
      });
    }
    // unknown `use`: the first configured stage whose use isn't in the stub.
    for (const raw of config.stages) {
      const use = typeof raw === "string" ? raw : (raw as StageSpec).use;
      if (!reg.has(use)) return JSON.stringify({ err: { kind: "unknown_stage", use } });
    }
    throw e;
  }
}

export function applyDecisionJsonPure(inputStr: string): string {
  const arg = JSON.parse(inputStr) as {
    decision: { skip?: string[]; abort?: boolean; insert?: string[]; reason?: string };
    remaining: Array<{ name: string; use: string; config?: Record<string, unknown>; on_error?: string; skip_if?: string }>;
  };
  const decision = makeDecision({
    skip: arg.decision.skip ?? [],
    abort: arg.decision.abort ?? false,
    insert: arg.decision.insert ?? [],
    reason: arg.decision.reason ?? "",
  });
  const remaining: PlannedStage[] = arg.remaining.map((r) => ({
    name: r.name,
    stage: null as unknown as Stage,
    spec: makeStageSpec({
      use: r.use,
      name: r.name,
      onError: (r.on_error ?? "continue") as StageSpec["onError"],
      ...(r.skip_if !== undefined ? { skipIf: r.skip_if } : {}),
    }),
    config: r.config ?? {},
  }));
  const reg = new StubRegistry();
  for (const name of decision.insert) reg.add(name, { name, produces: [], consumes: [] });
  const ctx: PipeContext = makePipeContext();
  const next = Router.apply(decision, remaining, ctx, reg.asRegistry());
  const out: Record<string, unknown> = { remaining: next.map(plannedToDict) };
  const note = ctx.reasoning["_router"];
  if (note !== undefined) out.router_note = note;
  return JSON.stringify(out);
}

const BUILTINS: Record<string, (ctx: PipeContext) => unknown> = {
  severity_gate: severityGate,
  pii_router: piiRouter,
  row_count_gate: rowCountGate,
};

export function evaluateBuiltinJsonPure(inputStr: string): string {
  const arg = JSON.parse(inputStr) as {
    name: string;
    ctx?: { artifacts?: Record<string, unknown>; metadata?: Record<string, unknown> };
  };
  const fn = BUILTINS[arg.name];
  if (!fn) return "null";
  const ctx = makePipeContext({
    artifacts: arg.ctx?.artifacts ?? {},
    metadata: arg.ctx?.metadata ?? {},
  });
  const d = fn(ctx) as { skip: string[]; abort: boolean; insert: string[]; reason: string } | null;
  if (d === null) return "null";
  return JSON.stringify({ skip: d.skip, abort: d.abort, insert: d.insert, reason: d.reason });
}

export function autoConfigJsonPure(inputStr: string): string {
  const arg = JSON.parse(inputStr) as { available: string[]; identity_opts?: Record<string, unknown> };
  const reg = new StubRegistry();
  for (const name of arg.available) reg.add(name, { name, produces: [], consumes: [] });
  const p = new Pipeline({ registry: reg.asRegistry(), identityOpts: arg.identity_opts ?? {} });
  // autoConfig is private; expose it via a tiny internal accessor OR call the
  // public path. Simplest: add a package-internal method. See Step 5 note.
  const cfg = (p as unknown as { autoConfig(): { pipeline: string; stages: StageSpec[]; decisions: string[] } }).autoConfig();
  return JSON.stringify({
    pipeline: cfg.pipeline,
    stages: cfg.stages.map((s) => ({ use: s.use, needs: s.needs, on_error: s.onError, config: s.config })),
    decisions: cfg.decisions,
  });
}

export function skipIfFalsyJsonPure(inputStr: string): string {
  return JSON.stringify(isFalsy(JSON.parse(inputStr)));
}
```

- [ ] **Step 5: Make `autoConfig` reachable from the shim**

`autoConfig` is `private`. Rather than the `as unknown` cast shown above (brittle), prefer a clean seam: rename `private autoConfig()` to a package-internal method the shim can call, OR export a standalone `computeAutoConfig(registry, identityOpts)` helper from `pipeline.ts` and have BOTH the `Pipeline` instance and the shim call it (DRY). Recommended: extract `export function computeAutoConfig(registry: StageRegistry, identityOpts: Record<string, unknown>): PipelineConfig` in pipeline.ts, call it from `run()` and from the shim. Update Task 3's edit to live in that function. Adjust the shim's `autoConfigJsonPure` to call `computeAutoConfig(reg.asRegistry(), arg.identity_opts ?? {})` directly (no Pipeline instance needed).

- [ ] **Step 6: Add the additive `WiringError` fields at the throw site**

In `resolver.ts`, extend `WiringError` (additive, message unchanged):
```ts
export class WiringError extends Error {
  stage?: string;
  missing?: string;
  available?: string[];
  constructor(message: string, extra?: { stage: string; missing: string; available: string[] }) {
    super(message);
    this.name = "WiringError";
    if (extra) { this.stage = extra.stage; this.missing = extra.missing; this.available = extra.available; }
  }
}
```
At the throw site (resolver.ts:~44): pass `{ stage: name, missing: dep, available: [...availableArtifacts].sort() }` as the second arg. Existing consumers read only `.message` — unaffected.

- [ ] **Step 7: Commit** (CI runs Leg A on push — expect all 5 families green)

```bash
git add packages/typescript/goldenpipe/src/core/wasm/plannerJsonPure.ts \
        packages/typescript/goldenpipe/src/core/engine/runner.ts \
        packages/typescript/goldenpipe/src/core/engine/resolver.ts \
        packages/typescript/goldenpipe/src/core/pipeline.ts \
        packages/typescript/goldenpipe/tests/parity/planner-parity.test.ts
git commit -m "feat(goldenpipe): pure-TS plannerJson shim + Leg A parity gate (pure-TS == core vectors)"
```

---

### Task 5: `plannerJson.ts` reroute (deserialize side) + wire the five seams

Route each planner seam through the backend when one is registered; pure-TS otherwise. `plannerJson.ts` owns serialize → `backend.<fn>` → deserialize, reproducing the SAME rich TS objects pure-TS returns.

**Files:**
- Create: `packages/typescript/goldenpipe/src/core/wasm/plannerJson.ts`
- Modify: `engine/resolver.ts` (Resolver.resolve guard), `engine/router.ts` (Router.apply guard), `decisions.ts` (3 gates), `pipeline.ts` (`computeAutoConfig` guard), `engine/runner.ts` (isFalsy guard)
- Test: `packages/typescript/goldenpipe/tests/parity/reroute-agreement.test.ts`

- [ ] **Step 1: Write `plannerJson.ts`** (deserialize side; the serialize sides mirror the shim)

For each fn: build the core-shaped input JSON from the live TS objects, call the backend, parse. Critical correctness points (from spec review B1/M3/M4):
- `resolveViaWasm(config, registry)`: serialize `{ config, stages: registry.listAll() mapped to {key,name,produces,consumes} }` (key == name for a real registry). Parse `ok`/`err`. On `ok`, rebuild `PlannedStage[]` — re-correlate each returned `name` back to the LIVE `registry.get(name)` for `.stage`, rebuild `spec` via `makeStageSpec`, use returned `config`. On `err`, throw `WiringError` (with fields) or `Error(unknown stage)`; throw on any unexpected `kind` (incl `parse`).
- `applyDecisionViaWasm(decision, remaining, registry)`: serialize `{ decision, remaining: remaining.map(p => ({ name, use: p.spec.use, config: p.config, on_error: p.spec.onError, ...(p.spec.skipIf?{skip_if}:{}) })) }` — **full PlannedSpec, not just name/use** (B1). Parse `{ remaining, router_note? }`. Rebuild each returned entry: kept names → re-correlate to the ORIGINAL live `PlannedStage` (preserve `.stage`/`.config`/`.spec`); inserted names (not in the original remaining) → `registry.get(name)` for `.stage`. **Write `router_note` back to `ctx.reasoning["_router"]`** (M3). Return the rebuilt list.
- `evaluateBuiltinViaWasm(name, ctx)`: serialize `{ name, ctx: { artifacts: ctx.artifacts, metadata: ctx.metadata } }`. Parse `Decision | null` (`"null"` → null).
- `autoConfigViaWasm(available, identityOpts)`: serialize `{ available, identity_opts: identityOpts }`. Parse `PipelineConfig` (map `on_error`→`onError` etc. back to the TS shape via `makeStageSpec`).
- `skipIfFalsyViaWasm(value)`: `JSON.parse(backend.skipIfFalsyJson(JSON.stringify(value)))` as boolean.

Error-envelope helper:
```ts
function throwFromErr(err: { kind: string; [k: string]: unknown }): never {
  if (err.kind === "wiring")
    throw new WiringError(String(err.stage ?? "wiring"), {
      stage: String(err.stage), missing: String(err.missing), available: (err.available as string[]) ?? [],
    });
  if (err.kind === "unknown_stage")
    throw new Error(`Stage '${String(err.use)}' not found in registry`);
  throw new Error(`goldenpipe-wasm error kind '${err.kind}': ${JSON.stringify(err)}`); // incl. "parse"
}
```

- [ ] **Step 2: Wire the five guards** (pure-TS runs when no backend)

Each seam gains a top-of-function guard. Examples:

`resolver.ts` `Resolver.resolve`:
```ts
import { getPipeWasmBackend } from "../wasm/backend.js";
import { resolveViaWasm } from "../wasm/plannerJson.js";
// ...
resolve(config: PipelineConfig, registry: StageRegistry): ExecutionPlan {
  const b = getPipeWasmBackend();
  if (b) return resolveViaWasm(config, registry, b);
  // ...existing pure-TS body unchanged...
}
```
`router.ts` `Router.apply` → `applyDecisionViaWasm(decision, remaining, ctx, registry, b)` (note: pass `ctx` so it can write `router_note` back). `decisions.ts` each gate → `if (b) return parseDecisionOrNull(b.evaluateBuiltinJson(...))` via `evaluateBuiltinViaWasm("severity_gate", ctx, b)` etc. `pipeline.ts` `computeAutoConfig` → `if (b) return autoConfigViaWasm(available, identityOpts, b)`. `runner.ts` `isFalsy` call site (the `if (planned.spec.skipIf)` block) → when `b`, use `skipIfFalsyViaWasm(artifact, b)` instead of `isFalsy(artifact)` (leaf predicate only; loop untouched).

- [ ] **Step 3: Write a reroute-agreement test** (CI; proves the wired guards produce the same plan as pure-TS)

Register a fake backend that DELEGATES to the pure shim (so no real wasm needed to test the WIRING), then assert a seam produces pure-TS-equal output:
```ts
import { describe, it, expect, afterEach } from "vitest";
import { setPipeWasmBackend } from "../../src/core/wasm/backend.js";
import * as pure from "../../src/core/wasm/plannerJsonPure.js";
import { Resolver } from "../../src/core/engine/resolver.js";
// build a small real registry + config; capture pure-TS plan (no backend),
// then set backend = { resolveJson: pure.resolveJsonPure, ... }, capture wasm-path
// plan, and expect deep-equal on the serializable projection (name/use/config/onError).
```
This catches serialize/deserialize wiring bugs (e.g. B1 config drop) WITHOUT the wasm artifact. Keep it to resolve + apply_decision (the two with rich round-trips).

- [ ] **Step 4: Commit** (CI verifies wiring + agreement on push)

```bash
git add packages/typescript/goldenpipe/src/core/wasm/plannerJson.ts \
        packages/typescript/goldenpipe/src/core/engine/resolver.ts \
        packages/typescript/goldenpipe/src/core/engine/router.ts \
        packages/typescript/goldenpipe/src/core/decisions.ts \
        packages/typescript/goldenpipe/src/core/pipeline.ts \
        packages/typescript/goldenpipe/src/core/engine/runner.ts \
        packages/typescript/goldenpipe/tests/parity/reroute-agreement.test.ts
git commit -m "feat(goldenpipe): route five planner seams through WASM core when enabled (pure-TS default)"
```

---

### Task 6: Leg B parity gate (wasm == core vectors)

Same vectors as Leg A, driven through the real wasm backend. Skips when the artifact is absent (default checkout); MANDATORY in CI where Task 7 builds it.

**Files:**
- Test: `packages/typescript/goldenpipe/tests/parity/planner-wasm-parity.test.ts`

- [ ] **Step 1: Write the Leg B test** (mirror goldenflow's identifier wasm leg)

```ts
import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { readFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { enableWasm, disableWasm } from "../../src/core/wasm/index.js";
import { getPipeWasmBackend } from "../../src/core/wasm/backend.js";

const ARTIFACT = fileURLToPath(
  new URL("../../src/core/wasm/artifacts/goldenpipe_wasm_bg.wasm", import.meta.url),
);
const VEC = (n: string) =>
  fileURLToPath(new URL(`../../../../rust/extensions/goldenpipe-core/tests/vectors/${n}.json`, import.meta.url));
const load = (n: string) => JSON.parse(readFileSync(VEC(n), "utf8")) as Array<{ input: unknown; expected: unknown }>;

const has = existsSync(ARTIFACT);
const d = has ? describe : describe.skip; // skip locally (no artifact); CI builds it

d("Leg B — goldenpipe-wasm == golden vectors", () => {
  beforeAll(async () => { await enableWasm({ require: true }); }); // URL/fs default -> the built artifact
  afterAll(() => disableWasm());

  const call = (fam: string, s: string): string => {
    const b = getPipeWasmBackend()!;
    return { resolve: b.resolveJson, apply_decision: b.applyDecisionJson, evaluate_builtin: b.evaluateBuiltinJson, auto_config: b.autoConfigJson, skip_if: b.skipIfFalsyJson }[fam]!(s);
  };
  for (const fam of ["resolve", "apply_decision", "evaluate_builtin", "auto_config", "skip_if"]) {
    it(`${fam} vectors`, () => {
      for (const { input, expected } of load(fam)) {
        expect(JSON.parse(call(fam, JSON.stringify(input)))).toEqual(expected);
      }
    });
  }
});
```

- [ ] **Step 2: Commit** (Leg B is `describe.skip` locally; Task 7's lane un-skips it)

```bash
git add packages/typescript/goldenpipe/tests/parity/planner-wasm-parity.test.ts
git commit -m "test(goldenpipe): Leg B wasm==core vector parity (skipped without artifact)"
```

---

### Task 7: CI `goldenpipe_wasm` lane

**Files:**
- Modify: `.github/workflows/ci.yml` (filter output + filter entry + the job). Mirror the `wasm_flow` lane (ci.yml:1685-1748).

- [ ] **Step 1: Add the filter output** (in the `changes` job `outputs:` block, near the other `*_wasm` lines)

```yaml
      goldenpipe_wasm: ${{ steps.filter.outputs.goldenpipe_wasm }}
```

- [ ] **Step 2: Add the filter entry** (in the `filters:` block)

```yaml
            goldenpipe_wasm:
              - 'packages/rust/extensions/goldenpipe-core/**'
              - 'packages/rust/extensions/goldenpipe-wasm/**'
              - 'packages/typescript/goldenpipe/**'
```

- [ ] **Step 3: Add the job** (mirror `wasm_flow`, ci.yml:1685-1748)

```yaml
  goldenpipe_wasm:
    needs: changes
    if: needs.changes.outputs.goldenpipe_wasm == 'true' || needs.changes.outputs.force_all == 'true'
    # Opt-in GoldenPipe WASM lane (SP3): builds goldenpipe-wasm into the
    # goldenpipe TS package's src/core/wasm/artifacts/ (where the loader
    # resolves it), then runs Leg A (pure-TS == core vectors) + Leg B
    # (wasm == core vectors) + the reroute-agreement + backend unit tests.
    # This is goldenpipe-wasm's CI home and the cross-surface drift gate;
    # goldenpipe-core is compiled here too (fmt/clippy/test) as the reference.
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@df4cb1c069e1874edd31b4311f1884172cec0e10  # v6
      - uses: dtolnay/rust-toolchain@29eef336d9b2848a0b548edc03f92a220660cdb8  # stable
        with:
          targets: wasm32-unknown-unknown
          components: rustfmt, clippy
      - uses: Swatinem/rust-cache@e18b497796c12c097a38f9edb9d0641fb99eee32  # v2
        with:
          workspaces: packages/rust/extensions/goldenpipe-wasm
      - uses: pnpm/action-setup@0e279bb959325dab635dd2c09392533439d90093  # v6.0.8
      - uses: actions/setup-node@48b55a011bda9f5d6aeb4c2d9c7362e8dae4041e  # v6.4.0
        with:
          node-version: 22
          cache: pnpm
      - run: pnpm install --frozen-lockfile
      - name: Lint the reference + wasm crates (fmt + clippy, -D warnings)
        run: |
          set -euo pipefail
          for C in goldenpipe-core goldenpipe-wasm; do
            cargo fmt   --manifest-path "packages/rust/extensions/$C/Cargo.toml" -- --check
            cargo clippy --manifest-path "packages/rust/extensions/$C/Cargo.toml" -- -D warnings
            cargo test  --manifest-path "packages/rust/extensions/$C/Cargo.toml"
          done
      - name: Build goldenpipe WASM artifact into the loader's artifacts/ dir
        # Mirrors wasm_flow: cargo build wasm32 + wasm-bindgen-cli pinned from
        # Cargo.lock, emitting goldenpipe_wasm_bg.wasm + .js where the loader
        # resolves `./artifacts/goldenpipe_wasm_bg.wasm`.
        run: |
          set -euo pipefail
          CRATE_DIR=packages/rust/extensions/goldenpipe-wasm
          OUT_DIR=packages/typescript/goldenpipe/src/core/wasm/artifacts
          cargo build --manifest-path "$CRATE_DIR/Cargo.toml" \
            --target wasm32-unknown-unknown --release
          WB_VER="$(grep -A1 '^name = "wasm-bindgen"$' "$CRATE_DIR/Cargo.lock" | grep '^version = ' | head -1 | sed -E 's/version = "([^"]+)"/\1/')"
          if [ -z "$WB_VER" ]; then echo "could not resolve wasm-bindgen version from Cargo.lock" >&2; exit 1; fi
          echo "Using wasm-bindgen $WB_VER"
          if ! wasm-bindgen --version 2>/dev/null | grep -q "$WB_VER"; then
            cargo install wasm-bindgen-cli --version "=$WB_VER" --locked
          fi
          wasm-bindgen \
            "$CRATE_DIR/target/wasm32-unknown-unknown/release/goldenpipe_wasm.wasm" \
            --target web --out-dir "$OUT_DIR" --out-name goldenpipe_wasm
      - name: Build shared wasm-runtime (the loader imports goldenmatch-wasm-runtime)
        run: pnpm --filter goldenmatch-wasm-runtime build
      - name: Planner parity + reroute + backend [THE GATE]
        # Leg A (pure-TS==core), Leg B un-skipped (artifact present), reroute
        # agreement, backend unit test.
        run: |
          pnpm --filter goldenpipe exec vitest run \
            tests/parity/planner-parity.test.ts \
            tests/parity/planner-wasm-parity.test.ts \
            tests/parity/reroute-agreement.test.ts \
            tests/unit/wasm-backend.test.ts
```

- [ ] **Step 4: Confirm `goldenpipe-wasm/Cargo.lock` exists** (the pin-from-lock step needs it)

The wasm-bindgen version is read from `Cargo.lock`. Run `cargo build --manifest-path packages/rust/extensions/goldenpipe-wasm/Cargo.toml --target wasm32-unknown-unknown` once (or `cargo generate-lockfile`) to produce the lock, and COMMIT it (goldenflow-wasm commits its lock). If the wasm target isn't installed locally, generate the lock with `cargo generate-lockfile --manifest-path .../Cargo.toml` and commit that.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/ci.yml packages/rust/extensions/goldenpipe-wasm/Cargo.lock
git commit -m "ci(goldenpipe): goldenpipe_wasm lane — Leg A/B parity + reroute gate (goldenpipe-core CI home)"
```

---

### Task 8: Docs sweep, PR, arm auto-merge, STOP

**Files:**
- Modify: doc surfaces per `.claude/doc-surfaces.md` (rollout-docs-sweep). At minimum: any goldenpipe README/tuning page listing surfaces or `enableWasm`, and the cross-surface roadmap note (`docs/design/2026-07-04-cross-surface-parity-roadmap.md`) — mark goldenpipe's TS/WASM leg landed.

- [ ] **Step 1: Run the rollout-docs-sweep** to inventory goldenpipe doc surfaces; update the ones that state goldenpipe's surface coverage or the `enableWasm` opt-in API. Add a short "Opt-in WASM planner (`enableWasm`)" note mirroring goldenflow's docs.

- [ ] **Step 2: Push the branch (benzsevern account)**

```bash
git push -u origin feat/goldenpipe-wasm
```

- [ ] **Step 3: Open the PR** (body: what SP3 ships, the drift-kill framing, the perf-gate-waived + opt-in-reroute honesty, Leg A/B gate, the autoConfig behavior change). Reference the spec + this plan.

- [ ] **Step 4: Arm auto-merge and STOP**

```bash
gh pr merge --auto --squash --delete-branch
```
Do NOT poll CI. Report the PR number and stop.

---

## Verification checklist (graduation, spec §8)

- [ ] `goldenpipe-wasm` host `cargo test`/`fmt`/`clippy` clean; wasm32 target builds in CI.
- [ ] Leg A green — pure-TS == core on all 5 vector families (drift-lock, incl. the `auto_config` identity vectors proving the conformance fix).
- [ ] Leg B green in CI — wasm == core on all 5 families (artifact built + un-skipped).
- [ ] Reroute-agreement test green — wired guards produce pure-TS-equal plans (catches B1-class serialize bugs).
- [ ] `autoConfig` identity conformance landed with the exact core condition (`!empty && has(IDENTITY)`), plumbing threaded through `PipelineOptions`.
- [ ] Pure-TS remains the default (no backend set → pure-TS runs); `enableWasm()` strictly opt-in.
- [ ] No perf gate (waived — planner, no hot loop; opt-in reroute has zero perf benefit by design).
