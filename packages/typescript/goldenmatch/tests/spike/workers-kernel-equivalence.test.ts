/**
 * Cloudflare Workers target of the cross-JS-target WASM equivalence gate (R1
 * Workstream A, kill-criterion 2). THE edge target the pure-TS scorer was written
 * for — the highest-signal probe. Runs inside the REAL Workers runtime (`workerd`)
 * via `@cloudflare/vitest-pool-workers` + `vitest.workers.config.ts`.
 *
 * Mirrors the Node spike's `pure-TS == score-wasm kernel @ 4dp` assertion through
 * the shared `runEquivalence` core. Workers is exactly where the loader's portability
 * is hardest, and it surfaced a REAL constraint (FINDING, R1 Workstream A): workerd
 * BANS runtime `WebAssembly.instantiate(bytes)` ("Wasm code generation disallowed by
 * embedder"). So in Workers the base64-decode-then-instantiate path does NOT work —
 * the bytes-from-base64 universal strategy is for Node/browser/Deno. The
 * Workers-NATIVE path is a STATIC `.wasm` import: the bundler compiles it to a
 * `WebAssembly.Module` at deploy time, and wasm-bindgen `--target web` instantiates
 * THAT Module (no runtime codegen). That's still NOT a per-target *hack* — it's the
 * one supported Workers mechanism, fed through the same glue + the same
 * `runEquivalence` comparator. (The score-wasm crate / glue / scorer code is
 * unchanged; only how the harness hands over the module differs by runtime.)
 *
 * SKIPS (passing) when the built artifacts are absent, mirroring the Node spike.
 * The CI `workers` job in r1-kernel-js-targets.yml builds the artifact first and
 * runs this un-skipped.
 */
import { describe, it, expect } from "vitest";
import { runEquivalence, type ScoreWasmGlue } from "./kernel-equivalence-core.js";
// STATIC `.wasm` import — the canonical @cloudflare/vitest-pool-workers form. The
// pool resolves it to a precompiled CompiledWasm `WebAssembly.Module` (default
// export) at BUILD time. This is the ONLY Workers-legal path: workerd BANS runtime
// codegen — both `WebAssembly.instantiate(bytes)` AND `new WebAssembly.Module(bytes)`
// throw "Wasm code generation disallowed by embedder" (verified in-env), so the
// base64-bytes universal path that works in Node/browser/Deno does NOT work here.
// That's not a per-target *hack* — it's the one supported Workers mechanism, fed
// through the SAME wasm-bindgen glue + the SAME runEquivalence comparator.
//
// Because the import is static, this file requires the built artifact present at
// transform time. The CI `workers` job builds it first; the absent-artifact skip
// the other targets keep doesn't apply to a static-import target (a default
// checkout simply doesn't run this config).
//
// PLAIN `.wasm` import (no `?module` suffix) — the canonical
// @cloudflare/vitest-pool-workers form. The pool's plugin resolves a bare `.wasm`
// import to a precompiled CompiledWasm `WebAssembly.Module` (default export) in
// the worker module graph. The earlier `.wasm?module` spelling tripped the HOST
// Vite `import-analysis` plugin (it tried to parse the `.wasm` as JS and the suite
// collected 0 tests) — `server.deps.inline` does NOT cover local source files, so
// the suffix had to go and `vitest.workers.config.ts` now lists the artifact dir
// in `assetsInclude` so the host treats it as an opaque asset, leaving resolution
// to the pool.
// @ts-expect-error — the `.wasm` default-export Module type is the Workers env's, not tsc's.
import scoreWasmModule from "../../src/core/wasm/artifacts/score_wasm_bg.wasm";

describe("workers: pure-TS == score-wasm kernel (4dp)", () => {
  it("kernel instantiates + reproduces the frozen pure-TS reference in workerd", async () => {
    const glue = (await import(
      "../../src/core/wasm/artifacts/score_wasm.js" as string
    )) as ScoreWasmGlue;
    const module = scoreWasmModule as unknown as WebAssembly.Module;

    const res = await runEquivalence(glue, module);
    // eslint-disable-next-line no-console
    console.log(
      `[workers-spike] ${res.comparisons} comparisons, max abs diff = ${res.maxDiff}`,
    );
    // Guard against a silent no-op: the corpus MUST have produced comparisons.
    expect(res.comparisons).toBeGreaterThan(0);
    expect(res.ok, `workers kernel != pure-TS @ 4dp: ${res.worst}`).toBe(true);
  });
});
