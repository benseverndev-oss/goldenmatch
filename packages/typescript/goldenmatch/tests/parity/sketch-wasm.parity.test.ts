/**
 * Cross-surface parity: the sketch (MinHash/LSH) wasm kernel reproduces the SAME
 * golden vectors as the Rust `sketch-core` (`tests/golden.rs`) and the Python
 * reference — the shared `sketch_golden.json` oracle. Validates the wasm per
 * stage (signature over the golden shingles; band hashes over the golden
 * signature; end-to-end band hashes from the raw text), so all three languages
 * agree byte-for-byte on the u64 hash family.
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import {
  signature,
  bandHashes,
  sketchBandHashes,
} from "../../src/core/sketchWasm.js";

interface GoldenCase {
  text: string;
  mode: string;
  k: number;
  num_perms: number;
  num_bands: number;
  seed: number;
  shingles: string[];
  signature: string[];
  band_hashes: string[];
}

const here = dirname(fileURLToPath(import.meta.url));
const cases: GoldenCase[] = JSON.parse(
  readFileSync(resolve(here, "fixtures/sketch/sketch_golden.json"), "utf8"),
);

const toBig = (arr: string[]): bigint[] => arr.map((s) => BigInt(s));

describe("sketch-wasm parity — reproduces the shared golden fixture", () => {
  it("has edge-case coverage (char + word modes)", () => {
    expect(cases.length).toBeGreaterThanOrEqual(10);
    expect(new Set(cases.map((c) => c.mode))).toEqual(new Set(["char", "word"]));
  });

  for (const c of cases) {
    it(`signature/band_hashes/end-to-end for ${JSON.stringify(c.text)} (${c.mode})`, () => {
      const seed = BigInt(c.seed);

      // MinHash signature over the golden shingle set.
      expect(signature(toBig(c.shingles), c.num_perms, seed)).toEqual(
        toBig(c.signature),
      );

      // Banded LSH bucket hashes over the golden signature.
      expect(bandHashes(toBig(c.signature), c.num_bands)).toEqual(
        toBig(c.band_hashes),
      );

      // End-to-end from raw text (shingle -> signature -> band hashes).
      expect(
        sketchBandHashes(c.text, c.mode, c.k, c.num_perms, c.num_bands, seed),
      ).toEqual(toBig(c.band_hashes));
    });
  }
});
