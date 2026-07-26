/**
 * fs-reroute.test.ts — the fs-default-ts-path PR2 reroute: the fs-wasm FS kernel
 * as the SOURCE OF TRUTH for TS probabilistic scoring, with pure-TS
 * `scoreProbabilistic` as the fallback.
 *
 * Covers: (1) eligibility gating, (2) the lean registry (default-off + enable/
 * disable), (3) the adapter builds correct kernel inputs (kernel-path == pure-TS
 * on a fully-observed block, where the two normalizations coincide), (4) the
 * intended #1854 DIVERGENCE on a partial-missing block (fixed full-field range
 * vs pure-TS per-pair shrinking range), (5) the pipeline reroute is wired and
 * behavior-preserving where the normalizations coincide.
 */
import { describe, it, expect, afterEach } from "vitest";
import type { MatchkeyConfig, Row } from "../../src/core/index.js";
import { runDedupePipeline, makeConfig, makeBlockingConfig } from "../../src/core/index.js";
import {
  fallbackResult,
  scoreProbabilistic,
} from "../../src/core/probabilistic.js";
import {
  fsRerouteEligible,
  buildFsBlockScoringInput,
  scoreProbabilisticFsBlock,
  enableFsWasmScoring,
  disableFsWasmScoring,
} from "../../src/core/fsScore.js";
import {
  getFsScoreBackend,
  isFsWasmScoringEnabled,
} from "../../src/core/fsScoreBackend.js";

afterEach(() => {
  // The registry is a module singleton — clear it so the reroute never leaks
  // into other test files (which expect the pure-TS default).
  disableFsWasmScoring();
});

function round4(pairs: readonly { idA: number; idB: number; score: number }[]) {
  return pairs
    .map((p) => [p.idA, p.idB, Number(p.score.toFixed(4))] as const)
    .sort((x, y) => x[0] - y[0] || x[1] - y[1]);
}

describe("fsRerouteEligible", () => {
  const base = (fields: MatchkeyConfig["fields"], extra: Partial<MatchkeyConfig> = {}): MatchkeyConfig => ({
    name: "mk",
    type: "probabilistic",
    fields,
    ...extra,
  }) as MatchkeyConfig;

  it("accepts kernel-expressible field scorers", () => {
    for (const scorer of ["jaro_winkler", "levenshtein", "token_sort", "exact", "ensemble"]) {
      expect(
        fsRerouteEligible(base([{ field: "f", transforms: [], scorer, weight: 1 }])),
      ).toBe(true);
    }
  });

  it("declines embedding / name-refdata / tf-adjustment fields (kernel can't express on TS)", () => {
    expect(fsRerouteEligible(base([{ field: "f", transforms: [], scorer: "embedding", weight: 1 }]))).toBe(false);
    expect(fsRerouteEligible(base([{ field: "f", transforms: [], scorer: "record_embedding", weight: 1 }]))).toBe(false);
    expect(fsRerouteEligible(base([{ field: "f", transforms: [], scorer: "name_freq_weighted_jw", weight: 1 }]))).toBe(false);
    expect(fsRerouteEligible(base([{ field: "f", transforms: [], scorer: "soundex_match", weight: 1 }]))).toBe(false);
    expect(
      fsRerouteEligible(base([{ field: "f", transforms: [], scorer: "jaro_winkler", weight: 1, tfAdjustment: true }])),
    ).toBe(false);
  });

  it("declines when a negative-evidence field uses a non-kernel scorer", () => {
    const mk = base(
      [{ field: "f", transforms: [], scorer: "jaro_winkler", weight: 1 }],
      { negativeEvidence: [{ field: "dob", scorer: "embedding", transforms: [], threshold: 0.5 }] },
    );
    expect(fsRerouteEligible(mk)).toBe(false);
  });

  it("declines non-probabilistic and empty-field matchkeys", () => {
    expect(fsRerouteEligible({ name: "x", type: "exact", fields: [{ field: "e", transforms: [], scorer: "exact", weight: 1 }] } as MatchkeyConfig)).toBe(false);
    expect(fsRerouteEligible(base([]))).toBe(false);
  });
});

describe("fs-scoring lean registry", () => {
  it("is default-off and enable/disable toggles it", () => {
    expect(getFsScoreBackend()).toBeNull();
    expect(isFsWasmScoringEnabled()).toBe(false);
    enableFsWasmScoring();
    expect(getFsScoreBackend()).not.toBeNull();
    expect(isFsWasmScoringEnabled()).toBe(true);
    disableFsWasmScoring();
    expect(getFsScoreBackend()).toBeNull();
  });
});

// A fully-observed NE + custom-banding block (mirrors the fs-wasm additive parity
// setup): every field present, band edges chosen so the mid-band similarity can't
// flip a level, so the kernel's FIXED full-field range EQUALS the pure-TS per-pair
// range (all fields observed) -> the reroute reproduces pure-TS exactly here.
const observedRows: Row[] = [
  { __row_id__: 0, name: "robert", code: "A1", dob: "1990" },
  { __row_id__: 1, name: "robert", code: "A1", dob: "1990" },
  { __row_id__: 2, name: "rupert", code: "A1", dob: "1985" },
];
const observedMk: MatchkeyConfig = {
  name: "fs_ne_banding",
  type: "probabilistic",
  fields: [
    { field: "name", scorer: "jaro_winkler", transforms: [], weight: 1.0, levels: 3, levelThresholds: [0.95, 0.3] },
    { field: "code", scorer: "exact", transforms: [], weight: 1.0, levels: 2, partialThreshold: 0.9 },
  ],
  negativeEvidence: [{ field: "dob", scorer: "exact", transforms: [], threshold: 0.5 }],
  linkThreshold: 0.5,
};

describe("fsScore adapter", () => {
  it("builds a kernel input mirroring the matchkey + EM (ids, banding, NE marshaling)", () => {
    const em = fallbackResult(observedMk);
    const input = buildFsBlockScoringInput(
      observedRows,
      observedMk as Extract<MatchkeyConfig, { type: "probabilistic" }>,
      em,
      0.0,
    );
    // scorer ids: jaro_winkler=0, exact=3.
    expect(input.scorerIds).toEqual([0, 3]);
    expect(input.levels).toEqual([3, 2]);
    // custom banding carried for name only (null for the default-banded code).
    expect(input.levelThresholds).toEqual([[0.95, 0.3], null]);
    // NE marshaled: exact scorer (3), threshold 0.5, fired weight = EM __ne__dob[0].
    expect(input.neScorerIds).toEqual([3]);
    expect(input.neThresholds).toEqual([0.5]);
    expect(input.neWeights).toEqual([em.matchWeights["__ne__dob"]![0]]);
    // linear normalization (posterior stays host-side).
    expect(input.calibrated).toBe(false);
    expect(input.priorW).toBe(0);
    expect(input.blockSizes).toEqual([observedRows.length]);
  });

  it("reproduces pure-TS scoreProbabilistic on a fully-observed block (normalizations coincide)", () => {
    const em = fallbackResult(observedMk);
    const kernel = round4(scoreProbabilisticFsBlock(observedRows, observedMk, em, 0.0));
    const pureTs = round4(scoreProbabilistic(observedRows, observedMk, em, { threshold: 0.0 }));
    expect(kernel.length).toBe(3);
    expect(kernel).toEqual(pureTs);
  });

  it("DIVERGES from pure-TS on a partial-missing block (the #1854 fixed-range alignment)", () => {
    // city is null on row 2 -> pure-TS normalizes that pair over a SHRUNKEN
    // per-pair range (city excluded), while the kernel uses the FIXED full-field
    // range -> different normalized scores for pairs touching the missing cell.
    const rows: Row[] = [
      { __row_id__: 0, name: "robert", city: "paris" },
      { __row_id__: 1, name: "robert", city: "paris" },
      { __row_id__: 2, name: "robert", city: null },
    ];
    const mk: MatchkeyConfig = {
      name: "fs_missing",
      type: "probabilistic",
      fields: [
        { field: "name", scorer: "jaro_winkler", transforms: [], weight: 1.0, levels: 2, partialThreshold: 0.8 },
        { field: "city", scorer: "jaro_winkler", transforms: [], weight: 1.0, levels: 2, partialThreshold: 0.8 },
      ],
      linkThreshold: 0.5,
    };
    const em = fallbackResult(mk);
    const kernel = round4(scoreProbabilisticFsBlock(rows, mk, em, -1));
    const pureTs = round4(scoreProbabilistic(rows, mk, em, { threshold: -1 }));
    // The (0,2)/(1,2) pairs (city missing) must score DIFFERENTLY under the two
    // normalizations; the fully-observed (0,1) pair is identical.
    expect(kernel).not.toEqual(pureTs);
    const p01k = kernel.find((p) => p[0] === 0 && p[1] === 1)!;
    const p01t = pureTs.find((p) => p[0] === 0 && p[1] === 1)!;
    expect(p01k[2]).toBe(p01t[2]);
    const p02k = kernel.find((p) => p[0] === 0 && p[1] === 2)!;
    const p02t = pureTs.find((p) => p[0] === 0 && p[1] === 2)!;
    expect(p02k[2]).not.toBe(p02t[2]);
  });
});

describe("pipeline reroute wiring", () => {
  it("uses the kernel when enabled and is behavior-preserving on a fully-observed block", async () => {
    const rows: Row[] = [
      { name: "John", zip: "x" },
      { name: "Jon", zip: "x" },
      { name: "John", zip: "x" },
      { name: "Mary", zip: "y" },
      { name: "Mary", zip: "y" },
    ];
    const mk: MatchkeyConfig = {
      name: "fs",
      type: "probabilistic",
      fields: [{ field: "name", transforms: [], scorer: "jaro_winkler", weight: 1, levels: 3, partialThreshold: 0.8 }],
      linkThreshold: 0.5,
    };
    const config = makeConfig({
      matchkeys: [mk],
      blocking: makeBlockingConfig({ strategy: "static", keys: [{ fields: ["zip"], transforms: [] }] }),
    });

    // Fully-observed single-field block: kernel FIXED range == pure-TS per-pair
    // range, so clustering is identical whether the reroute is on or off — proves
    // the branch is wired AND safe.
    disableFsWasmScoring();
    const off = await runDedupePipeline(rows, config);
    enableFsWasmScoring();
    expect(isFsWasmScoringEnabled()).toBe(true);
    const on = await runDedupePipeline(rows, config);

    const clusterSig = (r: Awaited<ReturnType<typeof runDedupePipeline>>) =>
      [...r.clusters.values()].map((c) => [...c.members].sort((a, b) => a - b)).sort((a, b) => (a[0] ?? 0) - (b[0] ?? 0));
    expect(clusterSig(on)).toEqual(clusterSig(off));
    expect(round4(on.scoredPairs)).toEqual(round4(off.scoredPairs));
  });
});
