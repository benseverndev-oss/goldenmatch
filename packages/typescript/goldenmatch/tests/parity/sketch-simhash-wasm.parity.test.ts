/**
 * SimHash wasm reroute parity. `simhash.ts::simhashSignature` — the projection
 * half of the semantic near-dup sketch (the seeded Rademacher matrix + dot
 * signs, the divergence-prone hand-rolled-BigInt part) — reroutes onto the
 * shared `sketch-core` kernel (`simhash_signature`) when the sketch wasm backend
 * is enabled. This proves:
 *   1. the WASM path reproduces the cross-language golden signature exactly, and
 *   2. WASM == pure-TS (so the Rust core is the source of truth and the pure-TS
 *      projection is a faithful fallback — no divergence risk).
 *
 * Golden: tests/fixtures/sketch_simhash_golden.json (from core/sketch.py via
 * scripts/gen_simhash_golden.py) — the same vectors the Rust
 * sketch-core/tests/simhash_golden.rs and the Python test_simhash_golden.py check.
 */
import { describe, it, expect, afterEach } from "vitest";
import { simhashSignature } from "../../src/core/simhash.js";
import { enableSketchWasm, disableSketchWasm } from "../../src/core/sketchWasm.js";
import goldenCases from "../fixtures/sketch_simhash_golden.json" with { type: "json" };

type Case = {
  label: string;
  vector: number[];
  num_planes: number;
  seed: number;
  signature: number[];
};
const cases = goldenCases as unknown as Case[];

afterEach(() => disableSketchWasm());

describe("simhash wasm reroute — signature parity + equivalence", () => {
  it("has a non-trivial fixture", () => {
    expect(cases.length).toBeGreaterThanOrEqual(8);
  });

  for (const c of cases) {
    it(`${c.label}: wasm signature == cross-language golden`, () => {
      enableSketchWasm();
      expect(simhashSignature(c.vector, c.num_planes, BigInt(c.seed))).toEqual(
        c.signature,
      );
    });

    it(`${c.label}: wasm == pure-TS (no divergence)`, () => {
      disableSketchWasm();
      const pureTs = simhashSignature(c.vector, c.num_planes, BigInt(c.seed));
      enableSketchWasm();
      const wasm = simhashSignature(c.vector, c.num_planes, BigInt(c.seed));
      expect(wasm).toEqual(pureTs);
      // And both equal the golden — the fallback is faithful.
      expect(pureTs).toEqual(c.signature);
    });
  }
});
