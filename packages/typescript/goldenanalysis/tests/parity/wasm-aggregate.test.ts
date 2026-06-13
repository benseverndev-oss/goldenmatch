/**
 * WASM-vs-pure-TS parity for goldenanalysis aggregates. The WASM kernel wraps
 * `analysis-core`, which is a line-for-line port of `aggregate.ts` (== Python),
 * so the binding assertion is WASM ≈ pure-TS to 4 decimals (expect exact). A few
 * hand-verified anchors cross-check absolute values.
 *
 * Skipped when the built artifact is absent (default checkout / no toolchain);
 * the CI `analysis_wasm` lane builds it first and runs this un-skipped.
 */
import { describe, it, expect, afterAll } from "vitest";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { histogram, quantile } from "../../src/core/aggregate.js";
import {
  enableAnalysisWasm,
  disableAnalysisWasm,
} from "../../src/core/wasm/index.js";

const artifact = fileURLToPath(
  new URL("../../src/core/wasm/artifacts/analysis_wasm_bg.wasm", import.meta.url),
);
const hasArtifact = existsSync(artifact);
const d = hasArtifact ? describe : describe.skip;

// Deterministic LCG so the random corpus is stable across runs/CI.
function makeRng(seed: number): () => number {
  let s = seed >>> 0;
  return () => {
    s = (s * 1103515245 + 12345) & 0x7fffffff;
    return s / 0x7fffffff;
  };
}

function randomArray(rng: () => number, n: number, lo: number, hi: number): number[] {
  return Array.from({ length: n }, () => lo + rng() * (hi - lo));
}

const rng = makeRng(2026);
const ARRAYS: number[][] = [
  [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
  [0, 0, 0, 10, 10, 10], // zero-count interior bins under bins >= 3
  [1, 2, 3, 4, 5], // exercises right-edge-inclusive max
  [-5, -3, 0, 2, 7, 9, -1], // negatives + positives
  randomArray(rng, 200, -50, 50),
  randomArray(rng, 1000, 0, 1),
  randomArray(rng, 37, 100, 1000),
];
const BINS = [2, 3, 4, 8, 16];
const QS = [0, 0.1, 0.25, 0.5, 0.75, 0.9, 1];

d("WASM aggregate parity", () => {
  afterAll(() => disableAnalysisWasm());

  it("enableAnalysisWasm() succeeds in this lane (artifact present)", async () => {
    disableAnalysisWasm();
    const ok = await enableAnalysisWasm();
    expect(ok).toBe(true);
    disableAnalysisWasm();
  });

  for (const arr of ARRAYS) {
    for (const bins of BINS) {
      it(`histogram(n=${arr.length}, bins=${bins}) WASM == pure-TS`, async () => {
        disableAnalysisWasm();
        const pure = histogram(arr, bins);
        const ok = await enableAnalysisWasm();
        expect(ok).toBe(true);
        const wasm = histogram(arr, bins);
        disableAnalysisWasm();
        expect(wasm.length).toBe(pure.length);
        for (let i = 0; i < pure.length; i++) {
          expect(wasm[i]![0]).toBeCloseTo(pure[i]![0]!, 4); // edge
          expect(wasm[i]![1]).toBe(pure[i]![1]); // count (exact)
        }
      });
    }
    for (const q of QS) {
      it(`quantile(n=${arr.length}, q=${q}) WASM == pure-TS`, async () => {
        disableAnalysisWasm();
        const pure = quantile(arr, q);
        const ok = await enableAnalysisWasm();
        expect(ok).toBe(true);
        const wasm = quantile(arr, q);
        disableAnalysisWasm();
        expect(wasm).toBeCloseTo(pure, 4);
      });
    }
  }

  // Hand-verified absolute anchors (cross-language sanity).
  it("anchors: quantile median + histogram edges", async () => {
    const ok = await enableAnalysisWasm();
    expect(ok).toBe(true);
    expect(quantile([1, 2, 3, 4], 0.5)).toBeCloseTo(2.5, 4);
    const h = histogram([0, 1, 2, 3], 2); // edges 0 and 1.5, counts 2 and 2
    expect(h.length).toBe(2);
    expect(h[0]![0]).toBeCloseTo(0, 4);
    expect(h[0]![1]).toBe(2);
    expect(h[1]![0]).toBeCloseTo(1.5, 4);
    expect(h[1]![1]).toBe(2);
    disableAnalysisWasm();
  });
});
