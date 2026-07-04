/**
 * Reroute equivalence for the MinHash-LSH blocker: with the sketch wasm backend
 * enabled the blocking path runs the shared sketch-core kernel; disabled, the
 * pure-TS BigInt arithmetic. Both must produce IDENTICAL band hashes — and thus
 * identical candidate pairs — which is what makes the Rust core the source of
 * truth (pure-TS = faithful fallback) and closes the silent-divergence gap.
 */
import { describe, it, expect, afterEach } from "vitest";
import { sketchBandHashes } from "../../src/core/sketch.js";
import { MinHashLSHBlocker } from "../../src/core/lshBlocker.js";
import {
  enableSketchWasm,
  disableSketchWasm,
} from "../../src/core/sketchWasm.js";

// A spread of near-duplicate + distinct records over both shingle modes.
const RECORDS = [
  "Acme Corporation",
  "Acme Corporaton", // typo
  "ACME CORP",
  "Globex International",
  "Globex Internatonal", // typo
  "Initech LLC",
  "Initech L.L.C.",
  "Umbrella Corp",
  "Stark Industries",
  "Wayne Enterprises",
];

afterEach(() => disableSketchWasm());

describe("sketch wasm reroute — MinHash-LSH equivalence", () => {
  it("sketchBandHashes: wasm == pure-TS for every record (both modes)", () => {
    for (const mode of ["char", "word"]) {
      const k = mode === "char" ? 3 : 1;
      for (const text of RECORDS) {
        disableSketchWasm();
        const pure = sketchBandHashes(text, mode, k, 32, 8, 42n);
        enableSketchWasm();
        const wasm = sketchBandHashes(text, mode, k, 32, 8, 42n);
        expect(wasm).toEqual(pure);
      }
    }
  });

  it("MinHashLSHBlocker.candidatePairs: wasm == pure-TS", () => {
    const blocker = new MinHashLSHBlocker("char", 3, 32, 8, 42n);

    disableSketchWasm();
    const pure = [...blocker.candidatePairs(RECORDS)].sort();
    enableSketchWasm();
    const wasm = [...blocker.candidatePairs(RECORDS)].sort();

    expect(wasm).toEqual(pure);
    // The typo pairs actually block together, so the reroute is exercised.
    expect(pure.length).toBeGreaterThan(0);
  });
});
