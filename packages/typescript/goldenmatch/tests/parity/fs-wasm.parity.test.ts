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
import {
  fallbackResult,
  fsWeightRange,
  scoreProbabilistic,
} from "../../src/core/probabilistic.js";
import type { MatchkeyConfig, Row } from "../../src/core/types.js";

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

// ---------------------------------------------------------------------------
// PR2 cross-language GATE: NE + custom banding + a partial-MISSING field. The
// fixture (`fs_block_scoring_ne_banding.json`) is AUTHORED by the SAME Python
// native `score_block_pairs_fs` (the reroute's oracle,
// `scripts/emit_fs_wasm_fixture.py`) with the level_thresholds + ne_* kwargs the
// fs-wasm entry marshals — and NO require_positive_evidence / missing_disagree
// (both default off), so the TS kernel reproduces Python-native EXACTLY. This is
// the case where the reroute's FIXED full-field normalization differs from the
// pure-TS per-pair shrinking range — the intended #1854 alignment.
// ---------------------------------------------------------------------------
interface NeBandingFixture extends Fixture {
  level_thresholds: (number[] | null)[];
  ne_values: (string | null)[][];
  ne_scorer_ids: number[];
  ne_thresholds: number[];
  ne_weights: number[];
}

const neFixture: NeBandingFixture = JSON.parse(
  readFileSync(
    resolve(here, "fixtures/fs/fs_block_scoring_ne_banding.json"),
    "utf8",
  ),
);

describe("fs-wasm NE + banding + missing cross-surface parity", () => {
  it("reproduces the native score_block_pairs_fs oracle with NE + custom banding + a missing field", () => {
    const pairs = scoreBlockPairsFs({
      rowIds: neFixture.row_ids,
      blockSizes: neFixture.block_sizes,
      fieldValues: neFixture.field_values,
      scorerIds: neFixture.scorer_ids,
      levels: neFixture.levels,
      partialThresholds: neFixture.partial_thresholds,
      matchWeights: neFixture.match_weights,
      calibrated: neFixture.calibrated,
      priorW: neFixture.prior_w,
      minWeight: neFixture.min_weight,
      weightRange: neFixture.weight_range,
      threshold: neFixture.threshold,
      levelThresholds: neFixture.level_thresholds,
      neValues: neFixture.ne_values,
      neScorerIds: neFixture.ne_scorer_ids,
      neThresholds: neFixture.ne_thresholds,
      neWeights: neFixture.ne_weights,
    });

    const got = pairs
      .map(([a, b, s]) => [a, b, Number(s.toFixed(6))] as const)
      .sort((x, y) => x[0] - y[0] || x[1] - y[1]);
    const want = neFixture.expected_pairs
      .map(([a, b, s]) => [a, b, Number(s.toFixed(6))] as const)
      .sort((x, y) => x[0] - y[0] || x[1] - y[1]);

    expect(got).toEqual(want);
  });
});

// ---------------------------------------------------------------------------
// ADDITIVE-marshaling proof: the widened fs-wasm entry carries Fellegi-Sunter
// negative evidence + custom level-banding through to the shared
// `fs_core::score_fs_pair` kernel. Oracle = the pure-TS `scoreProbabilistic`
// (the exact behavior PR 2 will reroute onto this kernel) on the SAME NE +
// banding config, driven by a deterministic `fallbackResult` EM model. Scorers
// are `exact` (id 3, tolerance-free 0/1) and `jaro_winkler` (id 0) with band
// edges 0.3 / 0.95 chosen so the mid-band similarity can't flip a level — so
// the summed weights (hence normalized scores) are identical, not merely close.
// ---------------------------------------------------------------------------
describe("fs-wasm additive NE + level-banding parity", () => {
  // 3 fully-observed rows. name: robert==robert (top band), rupert (mid band).
  // dob differs on row 2, so the negative-evidence field fires against it.
  const rows: Row[] = [
    { __row_id__: 0, name: "robert", code: "A1", dob: "1990" },
    { __row_id__: 1, name: "robert", code: "A1", dob: "1990" },
    { __row_id__: 2, name: "rupert", code: "A1", dob: "1985" },
  ];

  // Probabilistic matchkey: name gets CUSTOM 3-level banding via levelThresholds
  // (descending cutoffs 0.95 / 0.3), code is a default 2-level exact field, and
  // dob is a negative-evidence field (exact, fires when values disagree).
  const mk: MatchkeyConfig = {
    name: "fs_ne_banding",
    type: "probabilistic",
    fields: [
      {
        field: "name",
        scorer: "jaro_winkler",
        transforms: [],
        weight: 1.0,
        levels: 3,
        levelThresholds: [0.95, 0.3],
      },
      {
        field: "code",
        scorer: "exact",
        transforms: [],
        weight: 1.0,
        levels: 2,
        partialThreshold: 0.9,
      },
    ],
    negativeEvidence: [
      { field: "dob", scorer: "exact", transforms: [], threshold: 0.5 },
    ],
    emIterations: 20,
    convergenceThreshold: 0.001,
    linkThreshold: 0.5,
  };

  // Deterministic EM model (no sampling) so the oracle is reproducible.
  const em = fallbackResult(mk);
  const { minWeight, maxWeight } = fsWeightRange(em, mk);

  // scoreField scorer ids: jaro_winkler=0, exact=3 (fs_core::score_one / score-core).
  const kernelInput = {
    rowIds: [0, 1, 2],
    blockSizes: [3],
    fieldValues: [
      ["robert", "robert", "rupert"],
      ["A1", "A1", "A1"],
    ],
    scorerIds: [0, 3],
    levels: [3, 2],
    partialThresholds: [0.8, 0.9],
    matchWeights: [
      [...em.matchWeights["name"]!],
      [...em.matchWeights["code"]!],
    ],
    calibrated: false,
    priorW: 0.0,
    minWeight,
    weightRange: maxWeight - minWeight,
    threshold: 0.0,
    // The ADDITIVE fields under test:
    levelThresholds: [[0.95, 0.3], null] as (number[] | null)[],
    neValues: [["1990", "1990", "1985"]],
    neScorerIds: [3],
    neThresholds: [0.5],
    neWeights: [em.matchWeights["__ne__dob"]![0]!],
  };

  it("matches pure-TS scoreProbabilistic (the PR-2 reroute target) with NE + custom banding", () => {
    const wasm = scoreBlockPairsFs(kernelInput)
      .map(([a, b, s]) => [a, b, Number(s.toFixed(4))] as const)
      .sort((x, y) => x[0] - y[0] || x[1] - y[1]);

    const pureTs = scoreProbabilistic(rows, mk, em, { threshold: 0.0 })
      .map((p) => [p.idA, p.idB, Number(p.score.toFixed(4))] as const)
      .sort((x, y) => x[0] - y[0] || x[1] - y[1]);

    expect(wasm.length).toBe(3); // all within-block pairs emitted at threshold 0
    expect(wasm).toEqual(pureTs);
  });

  it("the NE + banding inputs actually change the output vs the default path", () => {
    const withEvidence = scoreBlockPairsFs(kernelInput)
      .map(([a, b, s]) => [a, b, Number(s.toFixed(6))] as const)
      .sort((x, y) => x[0] - y[0] || x[1] - y[1]);

    // Same block WITHOUT the additive fields (empty defaults => zero-config).
    const withoutEvidence = scoreBlockPairsFs({
      rowIds: kernelInput.rowIds,
      blockSizes: kernelInput.blockSizes,
      fieldValues: kernelInput.fieldValues,
      scorerIds: kernelInput.scorerIds,
      levels: kernelInput.levels,
      partialThresholds: kernelInput.partialThresholds,
      matchWeights: kernelInput.matchWeights,
      calibrated: kernelInput.calibrated,
      priorW: kernelInput.priorW,
      minWeight: kernelInput.minWeight,
      weightRange: kernelInput.weightRange,
      threshold: kernelInput.threshold,
    })
      .map(([a, b, s]) => [a, b, Number(s.toFixed(6))] as const)
      .sort((x, y) => x[0] - y[0] || x[1] - y[1]);

    // The pair whose dob disagrees (0,2)/(1,2) must be penalised by the fired
    // NE weight, so the two paths cannot be identical.
    expect(withEvidence).not.toEqual(withoutEvidence);
  });
});
