/**
 * Real-stack maybeSuggest tests: the trigger-gate + kill-switch + backend
 * cost-guarantee half of the dedupe-suggest wiring. Kept separate from
 * dedupe-suggest-wiring.test.ts because that file module-mocks maybeSuggest
 * (vitest mocks are file-scoped); here maybeSuggest runs for real against a
 * constructed DedupeResult whose postflightReport we choose, plus a spy backend.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { maybeSuggest } from "../../src/core/suggest.js";
import {
  setSuggestWasmBackend,
  disableSuggestWasm,
  type SuggestWasmBackend,
} from "../../src/core/suggestWasmBackend.js";
import { makeConfig } from "../../src/core/types.js";
import type {
  DedupeResult,
  Row,
  ClusterInfo,
  GoldenMatchConfig,
} from "../../src/core/types.js";
import type { PostflightReport } from "../../src/core/autoconfigVerify.js";

const rows: Row[] = [
  { __row_id__: 0, name: "alice" },
  { __row_id__: 1, name: "alicia" },
];

const config: GoldenMatchConfig = makeConfig({
  matchkeys: [
    {
      name: "person",
      type: "weighted",
      fields: [
        { field: "name", transforms: [], scorer: "jaro_winkler", weight: 1.0 },
      ],
      threshold: 0.5,
    },
  ],
});

function postflight(partial: Partial<PostflightReport>): PostflightReport {
  return {
    signals: {
      scoreHistogram: { bins: [], counts: [] },
      blockingRecall: "deferred",
      blockSizePercentiles: { p50: 0, p95: 0, p99: 0, max: 0 },
      thresholdOverlapPct: 0,
      totalPairsScored: 0,
      currentThreshold: 0.5,
      preliminaryClusterSizes: { p50: 0, p95: 0, p99: 0, max: 0, count: 0 },
      oversizedClusters: [],
    },
    adjustments: [],
    advisories: [],
    ...partial,
  };
}

function resultWith(report?: PostflightReport): DedupeResult {
  return {
    goldenRecords: [],
    clusters: new Map<number, ClusterInfo>(),
    dupes: [],
    unique: [],
    stats: {
      totalRecords: 2,
      totalClusters: 0,
      matchRate: 0,
      matchedRecords: 0,
      uniqueRecords: 2,
    },
    scoredPairs: [{ idA: 0, idB: 1, score: 0.9 }],
    config,
    suggestions: [],
    ...(report !== undefined ? { postflightReport: report } : {}),
  };
}

const CANNED = JSON.stringify([
  {
    id: "thr:raise:person",
    kind: "raise_threshold",
    target: "person",
    current_value: "0.50",
    proposed_value: "0.92",
    rationale: "raise it",
    predicted_effect: "precision_up",
    confidence: 0.7,
    patch: { op: "set_threshold", matchkey: "person", value: 0.92 },
    evidence: {},
  },
]);

describe("maybeSuggest trigger gate + kill-switch", () => {
  afterEach(() => {
    disableSuggestWasm();
    delete process.env.GOLDENMATCH_SUGGEST_ON_DEDUPE;
  });

  it("trigger off (no postflightReport): returns [] and never touches the backend", async () => {
    const spy = vi.fn(() => CANNED);
    const backend: SuggestWasmBackend = { suggestReview: spy };
    setSuggestWasmBackend(backend);

    const out = await maybeSuggest(resultWith(undefined), rows, config, {
      verify: false,
    });

    expect(out).toEqual([]);
    expect(spy).not.toHaveBeenCalled();
  });

  it("trigger on (bimodal postflight): calls the backend and parses candidates", async () => {
    const spy = vi.fn(() => CANNED);
    const backend: SuggestWasmBackend = { suggestReview: spy };
    setSuggestWasmBackend(backend);

    // advisories: [] (no "unimodal") => bimodal => headroom fires.
    const report = postflight({ adjustments: [], advisories: [] });
    const out = await maybeSuggest(resultWith(report), rows, config, {
      verify: false,
    });

    expect(spy).toHaveBeenCalledTimes(1);
    expect(out).toHaveLength(1);
    expect(out[0]!.id).toBe("thr:raise:person");
  });

  it("kill-switch (GOLDENMATCH_SUGGEST_ON_DEDUPE=0): [] even with trigger on", async () => {
    process.env.GOLDENMATCH_SUGGEST_ON_DEDUPE = "0";
    const spy = vi.fn(() => CANNED);
    const backend: SuggestWasmBackend = { suggestReview: spy };
    setSuggestWasmBackend(backend);

    const report = postflight({ adjustments: [], advisories: [] });
    const out = await maybeSuggest(resultWith(report), rows, config, {
      verify: false,
    });

    expect(out).toEqual([]);
    expect(spy).not.toHaveBeenCalled();
  });
});
