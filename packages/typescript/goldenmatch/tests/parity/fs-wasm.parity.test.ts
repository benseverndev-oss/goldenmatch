/**
 * Cross-surface Fellegi-Sunter parity: the TS/WASM surface must reproduce the
 * SAME block-scored pairs as the Python NATIVE kernel. The fixture
 * (`fixtures/fs/fs_block_scoring.json`) is AUTHORED by the Python native
 * `score_block_pairs_fs` (the oracle, `scripts/emit_fs_wasm_fixture.py`); fs-wasm
 * and the native kernel both call the SAME `fs_core::score_fs_pair`, so the pairs
 * + scores are byte-identical by construction. Scores are pinned to the fixture's
 * 6-decimal rounding.
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { scoreBlockPairsFs } from "../../src/core/fsWasm.js";

interface Fixture {
  field_values: (string | null)[][];
  row_ids: number[];
  block_sizes: number[];
  scorer_ids: number[];
  levels: number[];
  partial_thresholds: number[];
  match_weights: number[][];
  calibrated: boolean;
  prior_w: number;
  min_weight: number;
  weight_range: number;
  threshold: number;
  expected_pairs: [number, number, number][];
}

const here = dirname(fileURLToPath(import.meta.url));
const fixture: Fixture = JSON.parse(
  readFileSync(resolve(here, "fixtures/fs/fs_block_scoring.json"), "utf8"),
);

describe("fs-wasm cross-surface parity", () => {
  it("reproduces the native score_block_pairs_fs oracle pairs", () => {
    const pairs = scoreBlockPairsFs({
      rowIds: fixture.row_ids,
      blockSizes: fixture.block_sizes,
      fieldValues: fixture.field_values,
      scorerIds: fixture.scorer_ids,
      levels: fixture.levels,
      partialThresholds: fixture.partial_thresholds,
      matchWeights: fixture.match_weights,
      calibrated: fixture.calibrated,
      priorW: fixture.prior_w,
      minWeight: fixture.min_weight,
      weightRange: fixture.weight_range,
      threshold: fixture.threshold,
    });

    // Compare on (a, b) identity + 6-decimal score (the fixture's rounding).
    const got = pairs
      .map(([a, b, s]) => [a, b, Number(s.toFixed(6))] as const)
      .sort((x, y) => x[0] - y[0] || x[1] - y[1]);
    const want = fixture.expected_pairs
      .map(([a, b, s]) => [a, b, Number(s.toFixed(6))] as const)
      .sort((x, y) => x[0] - y[0] || x[1] - y[1]);

    expect(got).toEqual(want);
  });

  it("emits no pair below the threshold and keeps a < b ordering", () => {
    const pairs = scoreBlockPairsFs({
      rowIds: fixture.row_ids,
      blockSizes: fixture.block_sizes,
      fieldValues: fixture.field_values,
      scorerIds: fixture.scorer_ids,
      levels: fixture.levels,
      partialThresholds: fixture.partial_thresholds,
      matchWeights: fixture.match_weights,
      calibrated: fixture.calibrated,
      priorW: fixture.prior_w,
      minWeight: fixture.min_weight,
      weightRange: fixture.weight_range,
      threshold: fixture.threshold,
    });
    for (const [a, b, s] of pairs) {
      expect(a).toBeLessThan(b);
      expect(s).toBeGreaterThanOrEqual(fixture.threshold);
    }
  });
});
