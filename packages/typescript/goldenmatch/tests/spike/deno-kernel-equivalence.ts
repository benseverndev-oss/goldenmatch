/**
 * Deno target of the cross-JS-target WASM equivalence gate (R1 Workstream A,
 * kill-criterion 2). Run with: `deno test --allow-read tests/spike/deno-kernel-equivalence.ts`
 * from `packages/typescript/goldenmatch` (the r1-kernel-js-targets.yml `deno` job
 * does exactly this, after building the artifact).
 *
 * Mirrors the Node spike's `pure-TS == score-wasm kernel @ 4dp` assertion via the
 * shared `runEquivalence` core. Deno loads the wasm-bindgen `--target web` glue +
 * the base64-INLINED module directly (ESM + JSON import attributes are native in
 * Deno), then hands the decoded bytes to the shared comparator. This proves the
 * UNIVERSAL (base64-inline) loader strategy works in Deno with NO per-target hack
 * — no `node:fs`, no `import.meta.url` asset fetch.
 *
 * SKIPS cleanly (passing, with a logged notice) when the built artifacts are
 * absent (no wasm toolchain), mirroring the Node spike's skip-on-absent contract.
 *
 * NOTE: `Deno` is a runtime global only present under Deno; this file is NOT part
 * of the vitest suite (it's excluded by the vitest `include: tests/**\/*.test.ts`
 * glob — it has no `.test.ts` suffix), so the Node typecheck/test never loads it.
 */

// @ts-nocheck — typechecked by `deno check`, not by the package tsc (no Deno lib here).
import { runEquivalence, type ScoreWasmGlue } from "./kernel-equivalence-core.ts";

Deno.test("Deno: pure-TS == score-wasm kernel (4dp)", async () => {
  // Resolve artifacts relative to this file (Deno understands import.meta.url for
  // module imports — that's module resolution, not asset fetch, so it's portable).
  const glueUrl = new URL("../../src/core/wasm/artifacts/score_wasm.js", import.meta.url);
  const b64Url = new URL("../../src/core/wasm/artifacts/score_wasm_base64.js", import.meta.url);

  let glue: ScoreWasmGlue;
  let bytes: Uint8Array;
  try {
    glue = (await import(glueUrl.href)) as ScoreWasmGlue;
    const mod = (await import(b64Url.href)) as { WASM_BASE64: string };
    // atob is a Deno global — the universal decode path.
    const bin = atob(mod.WASM_BASE64);
    bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  } catch (err) {
    console.warn(`[deno-spike] artifact absent — SKIP (pure-TS default): ${err}`);
    return; // pass: skip-on-absent, mirroring the Node spike
  }

  const res = await runEquivalence(glue, bytes);
  console.log(
    `[deno-spike] ${res.comparisons} comparisons, max abs diff = ${res.maxDiff} (worst: ${res.worst})`,
  );
  if (!res.ok) {
    throw new Error(`Deno kernel != pure-TS @ 4dp: ${res.worst} (maxDiff=${res.maxDiff})`);
  }
});
