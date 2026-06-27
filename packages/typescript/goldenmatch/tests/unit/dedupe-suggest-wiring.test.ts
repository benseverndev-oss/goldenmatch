/**
 * dedupe() healer-wiring tests. The ROUTING half — verifies api.ts dispatches
 * to maybeSuggest / heal with the right verify flags, serializes with the right
 * `verified` flag, and that the advisory block never throws. maybeSuggest / heal
 * are spied (importActual keeps serializeSuggestions real) so the assertions
 * don't depend on postflight/score-distribution internals. The trigger-gate +
 * kill-switch + backend cost-guarantee are tested against the REAL maybeSuggest
 * in suggest-maybe.test.ts (vitest module mocks are file-scoped, so they can't
 * share a file with the real-stack tests).
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import type { Suggestion } from "../../src/core/suggest.js";
import type { Row, GoldenMatchConfig } from "../../src/core/types.js";

const { maybeSuggestMock, healMock } = vi.hoisted(() => ({
  maybeSuggestMock: vi.fn(),
  healMock: vi.fn(),
}));

// vi.mock is hoisted above the imports, so the static `dedupe` import below
// binds the mocked suggest exports (importActual keeps serializeSuggestions real).
vi.mock("../../src/core/suggest.js", async (orig) => {
  const actual = await orig<typeof import("../../src/core/suggest.js")>();
  return { ...actual, maybeSuggest: maybeSuggestMock, heal: healMock };
});

import { dedupe } from "../../src/core/api.js";

const rows: Row[] = [
  { email: "a@x.com", name: "alice" },
  { email: "a@x.com", name: "alice" },
  { email: "b@x.com", name: "bob" },
];

function candidate(): Suggestion {
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
    evidence: {},
  };
}

describe("dedupe() healer wiring (routing)", () => {
  beforeEach(() => {
    maybeSuggestMock.mockReset();
    healMock.mockReset();
  });

  it("default path: calls maybeSuggest with verify:false and serializes verified:false", async () => {
    maybeSuggestMock.mockResolvedValue([candidate()]);
    const result = await dedupe(rows, { exact: ["email"] });

    expect(maybeSuggestMock).toHaveBeenCalledTimes(1);
    const callArgs = maybeSuggestMock.mock.calls[0]!;
    expect(callArgs[3]).toEqual({ verify: false });
    expect(result.suggestions).toEqual([
      {
        id: "thr:raise:person",
        kind: "raise_threshold",
        target: "person",
        rationale: "raise it",
        verified: false,
        patch: { op: "set_threshold", matchkey: "person", value: 0.92 },
      },
    ]);
    expect(result.healTrail).toBeUndefined();
  });

  it("default path with empty suggestions yields suggestions: []", async () => {
    maybeSuggestMock.mockResolvedValue([]);
    const result = await dedupe(rows, { exact: ["email"] });
    expect(result.suggestions).toEqual([]);
  });

  it("suggest:true → maybeSuggest verify:true, serialized verified:true", async () => {
    maybeSuggestMock.mockResolvedValue([candidate()]);
    const result = await dedupe(rows, { exact: ["email"], suggest: true });

    expect(maybeSuggestMock.mock.calls[0]![3]).toEqual({ verify: true });
    expect(result.suggestions).toHaveLength(1);
    expect(result.suggestions[0]!.verified).toBe(true);
  });

  it("heal:true → healed config + healTrail (verified:true), heal beats suggest", async () => {
    const healedConfig = { threshold: 0.92 } as unknown as GoldenMatchConfig;
    healMock.mockResolvedValue({
      config: healedConfig,
      trail: [candidate()],
      result: dedupeBaseShape(healedConfig),
    });

    const result = await dedupe(rows, {
      exact: ["email"],
      heal: true,
      suggest: true,
    });

    expect(healMock).toHaveBeenCalledTimes(1);
    expect(maybeSuggestMock).not.toHaveBeenCalled(); // heal beats suggest
    expect(result.config).toBe(healedConfig);
    expect(result.healTrail).toEqual([
      {
        id: "thr:raise:person",
        kind: "raise_threshold",
        target: "person",
        rationale: "raise it",
        verified: true,
        patch: { op: "set_threshold", matchkey: "person", value: 0.92 },
      },
    ]);
    expect(result.suggestions).toEqual([]);
  });

  it("advisory block never throws: a failing maybeSuggest leaves the base result", async () => {
    maybeSuggestMock.mockRejectedValue(new Error("kernel exploded"));
    const result = await dedupe(rows, { exact: ["email"] });
    expect(result.suggestions).toEqual([]);
    expect(result.clusters.size).toBeGreaterThan(0);
  });
});

/** Minimal valid DedupeResult shape for the heal mock's `result` field. */
function dedupeBaseShape(config: GoldenMatchConfig) {
  return {
    goldenRecords: [],
    clusters: new Map(),
    dupes: [],
    unique: [],
    stats: {
      totalRecords: 0,
      totalClusters: 0,
      matchRate: 0,
      matchedRecords: 0,
      uniqueRecords: 0,
    },
    scoredPairs: [],
    config,
    suggestions: [],
  };
}
