/**
 * simhash.test.ts — cross-language parity for the SimHash/LSH sketch kernel.
 *
 * Replays the committed golden vectors (generated from the Python reference
 * `core/sketch.py` via `scripts/gen_simhash_golden.py`) through the TS port and
 * asserts byte-identical output. Signatures compare as `number[]` (0/1); band
 * hashes as DECIMAL STRINGS (`String(bigint)`) to dodge any Number precision
 * loss. Also pins the headline golden constants directly so a regression is
 * caught even if the fixture is ever regenerated wrong.
 */
import { describe, it, expect } from "vitest";
import { simhashSignature, simhashBandHashes } from "../../src/core/simhash.js";
import goldenCases from "../fixtures/sketch_simhash_golden.json" with { type: "json" };

interface GoldenCase {
  label: string;
  vector: number[];
  num_planes: number;
  num_bands: number;
  seed: number;
  signature: number[];
  band_hashes: string[];
}

const cases = goldenCases as unknown as GoldenCase[];

/** Render a bigint array as decimal strings for precision-safe comparison. */
function asDecimals(arr: readonly bigint[]): string[] {
  return arr.map((v) => String(v));
}

describe("simhash golden vectors (parity with core/sketch.py)", () => {
  for (const c of cases) {
    it(`${c.label}`, () => {
      const sig = simhashSignature(c.vector, c.num_planes, BigInt(c.seed));
      expect(sig).toEqual(c.signature);

      const bands = simhashBandHashes(sig, c.num_bands);
      expect(asDecimals(bands)).toEqual(c.band_hashes);
    });
  }
});

describe("simhash headline constants", () => {
  const V = [0.5, -0.3, 0.8, 0.1, -0.9, 0.4, -0.2, 0.7];

  it("simhashSignature(V, 8, 42n) === [1,1,1,1,1,0,1,1]", () => {
    expect(simhashSignature(V, 8, 42n)).toEqual([1, 1, 1, 1, 1, 0, 1, 1]);
  });

  it("simhashBandHashes([1,1,1,1,1,0,1,1], 4) matches the golden u64s", () => {
    expect(asDecimals(simhashBandHashes([1, 1, 1, 1, 1, 0, 1, 1], 4))).toEqual([
      "8326405673782927272",
      "10087387020540333614",
      "407431194778926956",
      "13491348438230804516",
    ]);
  });

  it("simhashSignature(V, 16, 7n) === [1,1,0,0,1,1,1,0,0,1,0,1,1,0,1,1]", () => {
    expect(simhashSignature(V, 16, 7n)).toEqual([
      1, 1, 0, 0, 1, 1, 1, 0, 0, 1, 0, 1, 1, 0, 1, 1,
    ]);
  });

  it("zero vector planes=8 => all-ones signature (every dot is a 0.0 tie)", () => {
    expect(simhashSignature([0, 0, 0, 0, 0, 0, 0, 0], 8, 42n)).toEqual([
      1, 1, 1, 1, 1, 1, 1, 1,
    ]);
  });
});

describe("simhash edge cases", () => {
  it("simhashBandHashes throws on a non-divisible signature length", () => {
    expect(() => simhashBandHashes([1, 0, 1, 0, 1, 0, 1, 0], 3)).toThrow();
  });

  it("empty vector planes=4 => all-ones signature (no planes to flip)", () => {
    expect(simhashSignature([], 4, 1n)).toEqual([1, 1, 1, 1]);
  });
});
