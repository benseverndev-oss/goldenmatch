/**
 * Cross-surface parity: the edge (wasm) in-house embedder reproduces the numpy
 * reference `GoldenEmbedModel.project` (`L2norm((feats @ W) + b)`) within cosine
 * tolerance — the shared `project_golden.json` oracle, the same file
 * `goldenembed-core/tests/project_parity.rs` checks. This is the edge-embedding
 * path that closes parity-roadmap P10: same char-n-gram featurize + projection
 * kernel as the Python / native / SQL surfaces, now runnable in a browser /
 * Worker with no ONNX Runtime.
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { createEmbedder, type EmbedModel } from "../../src/core/goldenembedWasm.js";

interface Case {
  dim: number;
  use_bias: boolean;
  featurizer: {
    n_features: number;
    ngram_min: number;
    ngram_max: number;
    lowercase: boolean;
    boundary: string;
    seed: number;
  };
  n_features: number;
  weights_b64: string;
  bias_b64: string | null;
  corpus: string[];
  expected_b64: string;
}

function f32(b64: string): Float32Array {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new Float32Array(bytes.buffer);
}

const here = dirname(fileURLToPath(import.meta.url));
const cases: Case[] = JSON.parse(
  readFileSync(resolve(here, "fixtures/goldenembed/project_golden.json"), "utf8"),
);

describe("goldenembed-wasm parity — edge embedding reproduces the numpy oracle", () => {
  it("has coverage", () => {
    expect(cases.length).toBeGreaterThanOrEqual(3);
  });

  for (const [ci, c] of cases.entries()) {
    it(`case ${ci} (dim=${c.dim}, bias=${c.use_bias})`, () => {
      const model: EmbedModel = {
        weights: f32(c.weights_b64),
        dim: c.dim,
        bias: c.bias_b64 ? f32(c.bias_b64) : undefined,
        nFeatures: c.n_features,
        ngramMin: c.featurizer.ngram_min,
        ngramMax: c.featurizer.ngram_max,
        lowercase: c.featurizer.lowercase,
        boundary: c.featurizer.boundary,
        seed: c.featurizer.seed,
      };
      const emb = createEmbedder(model);
      try {
        const got = emb.embed(c.corpus);
        const expected = f32(c.expected_b64);
        expect(got.length).toBe(expected.length);

        const d = c.dim;
        let worstCos = 0;
        let worstAbs = 0;
        for (let row = 0; row < c.corpus.length; row++) {
          const g = got.subarray(row * d, (row + 1) * d);
          const e = expected.subarray(row * d, (row + 1) * d);
          let enorm = 0;
          let dot = 0;
          for (let j = 0; j < d; j++) {
            worstAbs = Math.max(worstAbs, Math.abs(g[j]! - e[j]!));
            enorm += e[j]! * e[j]!;
            dot += g[j]! * e[j]!;
          }
          if (enorm > 0) worstCos = Math.max(worstCos, 1 - dot); // ~unit rows
        }
        expect(worstCos).toBeLessThan(1e-5);
        expect(worstAbs).toBeLessThan(1e-4);
      } finally {
        emb.free();
      }
    });
  }
});
