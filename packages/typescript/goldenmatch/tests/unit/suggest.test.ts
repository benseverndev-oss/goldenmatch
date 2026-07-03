import { describe, it, expect, afterEach } from "vitest";
import {
  serializeSuggestions,
  headroomSignal,
  suggestFromResult,
  type Suggestion,
} from "../../src/core/suggest.js";
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
} from "../../src/core/types.js";
import type { PostflightReport } from "../../src/core/autoconfigVerify.js";

// ---------------------------------------------------------------------------
// fixtures
// ---------------------------------------------------------------------------

function fullSuggestion(overrides: Partial<Suggestion> = {}): Suggestion {
  return {
    id: "thr:raise:person",
    kind: "raise_threshold",
    target: "person",
    currentValue: "0.50",
    proposedValue: "0.92",
    rationale: "raise it",
    predictedEffect: "precision_up",
    confidence: 0.7,
    patch: { op: "set_threshold", matchkey: "person", value: 0.92 },
    evidence: { mass_above: 1.0 },
    ...overrides,
  };
}

function emptyResult(): DedupeResult {
  return {
    goldenRecords: [],
    clusters: new Map<number, ClusterInfo>(),
    dupes: [],
    unique: [],
    stats: {
      totalRecords: 2,
      totalClusters: 1,
      matchRate: 0,
      matchedRecords: 0,
      uniqueRecords: 2,
    },
    scoredPairs: [{ idA: 0, idB: 1, score: 0.95 }],
    config: makeConfig({
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
    }),
    suggestions: [],
  };
}

const rows: Row[] = [
  { __row_id__: 0, name: "alice" },
  { __row_id__: 1, name: "alicia" },
];

function postflight(
  partial: Partial<PostflightReport>,
): PostflightReport {
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

// ---------------------------------------------------------------------------
// tests
// ---------------------------------------------------------------------------

describe("serializeSuggestions", () => {
  it("projects to the wire shape and plumbs the caller-supplied verified flag", () => {
    const out = serializeSuggestions([fullSuggestion()], { verified: true });
    expect(out).toEqual([
      {
        id: "thr:raise:person",
        kind: "raise_threshold",
        target: "person",
        rationale: "raise it",
        verified: true,
        patch: { op: "set_threshold", matchkey: "person", value: 0.92 },
      },
    ]);
  });

  it("carries verified:false through verbatim", () => {
    const out = serializeSuggestions([fullSuggestion()], { verified: false });
    expect(out[0]!.verified).toBe(false);
  });
});

describe("headroomSignal", () => {
  it("returns null on an undefined report", () => {
    expect(headroomSignal(undefined)).toBeNull();
  });

  it("returns null on a healthy (unimodal) report", () => {
    const r = postflight({
      advisories: ["score distribution is unimodal; threshold cannot be auto-set"],
    });
    expect(headroomSignal(r)).toBeNull();
  });

  it("fires on a proposed threshold adjustment", () => {
    const r = postflight({
      adjustments: [
        {
          field: "threshold",
          fromValue: 0.5,
          toValue: 0.92,
          reason: "valley differs",
          signal: "scoreHistogram",
        },
      ],
      advisories: ["score distribution is unimodal; threshold cannot be auto-set"],
    });
    const reason = headroomSignal(r);
    expect(reason?.signal).toBe("threshold_adjustment");
  });

  it("fires on a bimodal distribution (no unimodal advisory)", () => {
    const r = postflight({ adjustments: [], advisories: [] });
    const reason = headroomSignal(r);
    expect(reason?.signal).toBe("score_distribution");
  });
});

describe("suggestFromResult", () => {
  afterEach(() => disableSuggestWasm());

  it("returns [] when no backend is registered (graceful-empty)", async () => {
    const out = await suggestFromResult(emptyResult(), rows, { verify: false });
    expect(out).toEqual([]);
  });

  it("parses a stub backend's JSON into typed suggestions", async () => {
    const kernelJson = JSON.stringify([
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
        evidence: { mass_above: 1.0 },
      },
    ]);
    const stub: SuggestWasmBackend = { suggestReview: () => kernelJson };
    setSuggestWasmBackend(stub);

    const out = await suggestFromResult(emptyResult(), rows, { verify: false });
    expect(out).toHaveLength(1);
    expect(out[0]).toMatchObject({
      id: "thr:raise:person",
      kind: "raise_threshold",
      target: "person",
      currentValue: "0.50",
      proposedValue: "0.92",
      predictedEffect: "precision_up",
      confidence: 0.7,
      patch: { op: "set_threshold", matchkey: "person", value: 0.92 },
    });
  });

  it("returns [] when the backend throws (graceful-empty)", async () => {
    const stub: SuggestWasmBackend = {
      suggestReview: () => {
        throw new Error("boom");
      },
    };
    setSuggestWasmBackend(stub);
    const out = await suggestFromResult(emptyResult(), rows, { verify: false });
    expect(out).toEqual([]);
  });
});
