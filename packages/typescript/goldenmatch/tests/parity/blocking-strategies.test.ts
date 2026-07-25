/**
 * Cross-language parity for the `lsh` + `perceptual` blocking strategies
 * (Part C of the cross-runtime kernel-closure arc). The TS `buildLshBlocks` /
 * `buildPerceptualBlocks` must produce the SAME block membership as the Python
 * reference (`build_lsh_blocks` / `build_perceptual_blocks`) for the same rows +
 * config — they share the underlying kernel (`sketch-core`/`sketch.ts` for lsh;
 * the banded-hamming split for perceptual), so the bucketing is byte-parity.
 *
 * Blocks are compared by the `id` column (value-based, order-independent).
 * Goldens: fixtures/blocking-strategies.json (emit_blocking_parity_fixtures.py).
 */
import { describe, it, expect } from "vitest";
import { buildLshBlocks, buildPerceptualBlocks } from "../../src/core/blocker.js";
import type { BlockingConfig, Row } from "../../src/core/types.js";
import fixture from "./fixtures/blocking-strategies.json" with { type: "json" };

/** Each BlockResult -> its members' sorted `id`s; then sort the blocks. */
function blocksById(results: { rows: readonly Row[] }[]): string[][] {
  return results
    .map((b) => b.rows.map((r) => String(r["id"])).sort())
    .sort((x, y) => (x.join(",") < y.join(",") ? -1 : x.join(",") > y.join(",") ? 1 : 0));
}

// The Python emitter sorts blocks lexicographically by the id list; mirror that
// ordering so the two arrays compare element-wise.
function sortFixtureBlocks(blocks: string[][]): string[][] {
  return [...blocks]
    .map((b) => [...b].sort())
    .sort((x, y) => (x.join(",") < y.join(",") ? -1 : x.join(",") > y.join(",") ? 1 : 0));
}

describe("blocking-strategy parity — lsh", () => {
  it("reproduces the Python MinHash/LSH block membership", () => {
    const { rows, config, blocks } = fixture.lsh;
    const cfg: BlockingConfig = {
      strategy: "lsh",
      keys: [],
      maxBlockSize: 1000,
      skipOversized: false,
      lsh: {
        column: config.column,
        mode: config.mode as "char" | "word",
        k: config.k,
        numPerms: config.num_perms,
        seed: config.seed,
        numBands: config.num_bands,
      },
    };
    const got = blocksById(buildLshBlocks(rows as Row[], cfg));
    expect(got).toEqual(sortFixtureBlocks(blocks));
  });
});

describe("blocking-strategy parity — perceptual", () => {
  it("reproduces the Python banded-hamming block membership", () => {
    const { rows, config, blocks } = fixture.perceptual;
    const cfg: BlockingConfig = {
      strategy: "perceptual",
      keys: [],
      maxBlockSize: 1000,
      skipOversized: false,
      perceptual: {
        column: config.column,
        numBands: config.num_bands,
        hashBits: config.hash_bits,
      },
    };
    const got = blocksById(buildPerceptualBlocks(rows as Row[], cfg));
    expect(got).toEqual(sortFixtureBlocks(blocks));
  });
});
