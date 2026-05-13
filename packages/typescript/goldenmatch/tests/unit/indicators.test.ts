/**
 * indicators.test.ts — Unit tests for the 5 complexity indicators + context.
 */
import { describe, it, expect } from "vitest";
import {
  computeColumnPriors,
  computeCorruptionScore,
  estimateSparseMatchSignal,
  estimateFullPopHits,
  computeCrossBlockingOverlap,
  computeIdentityCollisionSignal,
  IndicatorContext,
} from "../../src/core/indicators.js";
import type { Row, GoldenMatchConfig } from "../../src/core/types.js";

const cleanPeople: Row[] = [
  { first: "Alice", last: "Smith", email: "alice@example.com", zip: "10001" },
  { first: "Bob", last: "Jones", email: "bob@example.com", zip: "10002" },
  { first: "Carol", last: "Davis", email: "carol@example.com", zip: "10003" },
  { first: "David", last: "Wilson", email: "david@example.com", zip: "10004" },
];

const dirtyPeople: Row[] = [
  { first: "Alice", last: "Smith", email: "alice@example.com" },
  { first: "alice", last: "smith", email: "ALICE@example.com" },
  { first: "Alise", last: "Smyth", email: "alise@example.com" },
  { first: "Bob", last: "Jones", email: "bob@example.com" },
  { first: "BOB", last: "JONES", email: "bob@example.com" },
];

const sparseRows: Row[] = [
  { a: "x", b: null },
  { a: null, b: "y" },
  { a: null, b: null },
];

describe("computeColumnPriors", () => {
  it("returns empty for empty data", () => {
    expect(computeColumnPriors([])).toEqual({});
  });

  it("scores email column high on identity", () => {
    const priors = computeColumnPriors(cleanPeople);
    expect(priors.email).toBeDefined();
    expect(priors.email!.identityScore).toBeGreaterThanOrEqual(0.7);
  });

  it("scores high-cardinality strings at 0.7", () => {
    const priors = computeColumnPriors(cleanPeople);
    // `first` is unique across rows but not an identity-name pattern,
    // so should hit the cardinality_ratio > 0.5 branch (0.7).
    expect(priors.first?.identityScore).toBeCloseTo(0.7, 5);
  });

  it("flags corruption when case/whitespace produces duplicates", () => {
    const priors = computeColumnPriors(dirtyPeople);
    // "Alice" and "alice" collapse to same normalized form; corruption > 0
    expect(priors.first!.corruptionScore).toBeGreaterThan(0);
    expect(priors.email!.corruptionScore).toBeGreaterThan(0);
  });
});

describe("computeCorruptionScore", () => {
  it("is 0 on clean unique values", () => {
    expect(computeCorruptionScore(cleanPeople, "email")).toBe(0);
  });
  it("is > 0 on case-collisions", () => {
    expect(computeCorruptionScore(dirtyPeople, "email")).toBeGreaterThan(0);
  });
  it("is 0 for unknown column", () => {
    expect(computeCorruptionScore(cleanPeople, "doesnotexist")).toBe(0);
  });
});

describe("estimateSparseMatchSignal", () => {
  it("marks empty/no-exact-cols as sparse", () => {
    const v = estimateSparseMatchSignal(cleanPeople, { exactColumns: [] });
    expect(v.isSparse).toBe(true);
    expect(v.estimatedNTruePairs).toBe(0);
  });
  it("returns 0 pairs when all values distinct", () => {
    const v = estimateSparseMatchSignal(cleanPeople, { exactColumns: ["email"] });
    expect(v.estimatedNTruePairs).toBe(0);
    expect(v.isSparse).toBe(true);
  });
  it("counts pair collisions", () => {
    const v = estimateSparseMatchSignal(dirtyPeople, { exactColumns: ["email"] });
    // bob@example.com appears twice → 1 pair
    expect(v.estimatedNTruePairs).toBe(1);
  });
});

describe("estimateFullPopHits", () => {
  it("returns 0 on unknown column", () => {
    expect(estimateFullPopHits(cleanPeople, "missing")).toBe(0);
  });
  it("counts collision pairs", () => {
    expect(estimateFullPopHits(dirtyPeople, "email")).toBe(1);
  });
  it("returns 0 when all values unique", () => {
    expect(estimateFullPopHits(cleanPeople, "email")).toBe(0);
  });
});

describe("computeCrossBlockingOverlap", () => {
  it("returns 1.0 for identical keys", () => {
    expect(computeCrossBlockingOverlap(cleanPeople, "email", "email")).toBe(1.0);
  });

  it("returns 1.0 when no co-blocked pairs exist (degenerate)", () => {
    // All values unique under both keys → empty union → degenerate 1.0.
    const v = computeCrossBlockingOverlap(cleanPeople, "first", "last");
    expect(v).toBe(1.0);
  });

  it("computes overlap when both keys produce same groupings", () => {
    const rows: Row[] = [
      { city: "NY", zip: "100" },
      { city: "NY", zip: "100" },
      { city: "LA", zip: "200" },
      { city: "LA", zip: "200" },
    ];
    expect(computeCrossBlockingOverlap(rows, "city", "zip")).toBe(1.0);
  });

  it("returns lower overlap on partially-orthogonal keys", () => {
    const rows: Row[] = [
      { city: "NY", category: "A" },
      { city: "NY", category: "B" },
      { city: "LA", category: "A" },
      { city: "LA", category: "B" },
    ];
    const v = computeCrossBlockingOverlap(rows, "city", "category");
    expect(v).not.toBeNull();
    expect(v!).toBeLessThan(1.0);
    expect(v!).toBeGreaterThanOrEqual(0.0);
  });
});

describe("computeIdentityCollisionSignal", () => {
  it("returns 0 when no multi-record groups", () => {
    const sig = computeIdentityCollisionSignal(cleanPeople, "email", ["first"]);
    expect(sig.rate).toBe(0);
  });

  it("returns 0 when witnesses missing", () => {
    const sig = computeIdentityCollisionSignal(cleanPeople, "email", []);
    expect(sig.rate).toBe(0);
  });

  it("detects high divergence on shared identity with different witnesses", () => {
    const rows: Row[] = [
      { email: "shared@example.com", name: "Alice Anderson" },
      { email: "shared@example.com", name: "Zachary Zykov" },
      { email: "other@example.com", name: "Bob" },
    ];
    const sig = computeIdentityCollisionSignal(rows, "email", ["name"]);
    expect(sig.rate).toBeGreaterThan(0);
    expect(sig.witnessUsed).toBe("name");
  });
});

describe("IndicatorContext memoization", () => {
  const config: GoldenMatchConfig = {
    matchkeys: [],
    blocking: { strategy: "static", keys: [], maxBlockSize: 1000, skipOversized: true },
    threshold: 0.85,
  };

  it("caches column priors", () => {
    const ctx = new IndicatorContext(cleanPeople, config);
    const a = ctx.columnPriors;
    const b = ctx.columnPriors;
    expect(a).toBe(b);
  });

  it("caches sparsity verdict", () => {
    const ctx = new IndicatorContext(cleanPeople, config);
    const a = ctx.sparsityVerdict;
    const b = ctx.sparsityVerdict;
    expect(a).toBe(b);
  });

  it("hasFired side-channel one-shots", () => {
    const ctx = new IndicatorContext(cleanPeople, config);
    expect(ctx.hasFired("rule_x")).toBe(false);
    ctx.markFired("rule_x");
    expect(ctx.hasFired("rule_x")).toBe(true);
  });

  it("survives sparse data", () => {
    const ctx = new IndicatorContext(sparseRows, config);
    expect(() => ctx.columnPriors).not.toThrow();
  });
});
