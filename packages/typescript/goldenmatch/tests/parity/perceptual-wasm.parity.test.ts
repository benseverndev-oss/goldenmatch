/**
 * Cross-surface parity: the wasm image pHash must reproduce the Python reference
 * BYTE-EXACT (not within-K-bits). This holds because the DCT basis is a committed
 * constant table shared by the Rust kernel and the Python reference — no runtime
 * libm divergence — so a JS-computed hash equals a Python-built one bit-for-bit.
 *
 * Fixtures are the image entries of the Python golden vector
 * (`packages/python/goldenmatch/tests/fixtures/perceptual_golden.json`), copied
 * here by hand-free extraction; the Rust core asserts the same file in golden.rs.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { describe, it, expect } from "vitest";
import { phashImage, hamming } from "../../src/core/perceptualWasm.js";

interface ImageFixture {
  name: string;
  pixels: number[][];
  phash: string;
}

const here = dirname(fileURLToPath(import.meta.url));
const fixtures: { images: ImageFixture[] } = JSON.parse(
  readFileSync(resolve(here, "fixtures/perceptual/images.json"), "utf8"),
);

describe("perceptual wasm <-> Python pHash parity (byte-exact)", () => {
  it("has fixtures", () => {
    expect(fixtures.images.length).toBeGreaterThan(0);
  });

  for (const img of fixtures.images) {
    it(`byte-exact pHash: ${img.name}`, () => {
      const got = phashImage(img.pixels);
      expect(got).toBe(img.phash);
      // and zero hamming distance to itself / the reference
      expect(hamming(got, img.phash)).toBe(0);
    });
  }

  it("hamming distance is symmetric + nonzero for distinct hashes", () => {
    const [a, b] = fixtures.images;
    if (a && b && a.phash !== b.phash) {
      expect(hamming(a.phash, b.phash)).toBeGreaterThan(0);
      expect(hamming(a.phash, b.phash)).toBe(hamming(b.phash, a.phash));
    }
  });
});
