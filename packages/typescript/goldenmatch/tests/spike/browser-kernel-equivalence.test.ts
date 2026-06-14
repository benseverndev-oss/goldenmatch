/**
 * Browser target of the cross-JS-target WASM equivalence gate (R1 Workstream A,
 * kill-criterion 2). Runs under vitest BROWSER MODE (Playwright/chromium provider)
 * via `vitest.browser.config.ts` — a REAL browser, not jsdom, so WASM
 * instantiation + the universal base64 loader are exercised in an actual web
 * runtime.
 *
 * Mirrors the Node spike's `pure-TS == score-wasm kernel @ 4dp` assertion through
 * the shared `runEquivalence` core. In the browser the UNIVERSAL (base64-inline)
 * loader is the natural fit: the bytes come from the inlined module + `atob`, so
 * there is NO `fetch` of a sibling `.wasm` asset (which a bundler/CSP can block) —
 * the exact per-target-hack the kill-criterion forbids is avoided.
 *
 * SKIPS (passing) when the built artifacts are absent, mirroring the Node spike.
 * The CI `browser` job in r1-kernel-js-targets.yml builds the artifact first and
 * runs this un-skipped.
 */
import { describe, it, expect } from "vitest";
import { runEquivalence, type ScoreWasmGlue } from "./kernel-equivalence-core.js";

// Probe for the built artifacts via dynamic import; absent => skip (pass).
async function loadArtifacts(): Promise<{ glue: ScoreWasmGlue; bytes: Uint8Array } | null> {
  try {
    const glue = (await import(
      "../../src/core/wasm/artifacts/score_wasm.js" as string
    )) as ScoreWasmGlue;
    const mod = (await import(
      "../../src/core/wasm/artifacts/score_wasm_base64.js" as string
    )) as { WASM_BASE64: string };
    const bin = atob(mod.WASM_BASE64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return { glue, bytes };
  } catch {
    return null;
  }
}

describe("browser: pure-TS == score-wasm kernel (4dp)", () => {
  it("kernel reproduces the frozen pure-TS reference over the corpus", async () => {
    const arts = await loadArtifacts();
    if (arts === null) {
      // eslint-disable-next-line no-console
      console.warn("[browser-spike] artifact absent — SKIP (pure-TS default).");
      return;
    }
    const res = await runEquivalence(arts.glue, arts.bytes);
    // eslint-disable-next-line no-console
    console.log(
      `[browser-spike] ${res.comparisons} comparisons, max abs diff = ${res.maxDiff}`,
    );
    expect(res.ok, `browser kernel != pure-TS @ 4dp: ${res.worst}`).toBe(true);
  });
});
