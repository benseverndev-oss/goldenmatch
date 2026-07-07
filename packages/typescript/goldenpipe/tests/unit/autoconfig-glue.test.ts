import { describe, it, expect } from "vitest";
import {
  profileContext,
  profileComplexity,
  enforceConfidence,
} from "../../src/core/autoconfigGlue.js";
import { PipeNotConfidentError } from "../../src/core/errors.js";
import type { PipePlan, PipeProfile } from "../../src/core/autoconfigPlanner.js";
import { planConfig } from "../../src/core/pipeline.js";
import { buildDefaultRegistry } from "../../src/core/adapters/index.js";

const financeRows = [
  { account_number: "A1", currency: "USD" },
  { account_number: "A2", currency: "EUR" },
];
const personRows = [
  { first_name: "John", last_name: "Smith", email: "j@x.co" },
  { first_name: "Jane", last_name: "Doe", email: "d@x.co" },
];

describe("profileContext", () => {
  it("detects finance domain from columns", () => {
    const p = profileContext(financeRows);
    expect(p.nRows).toBe(2);
    expect(p.columnNames).toEqual(["account_number", "currency"]);
    expect(p.inferredDomain).toBe("finance");
    expect(p.domainConfidence).toBe(1.0);
  });
  it("person columns detect no domain", () => {
    const p = profileContext(personRows);
    expect(p.inferredDomain).toBeNull();
    expect(p.domainConfidence).toBe(0);
  });
  it("empty rows -> zeros", () => {
    const p = profileContext([]);
    expect(p.nRows).toBe(0);
    expect(p.inferredDomain).toBeNull();
  });
});

describe("profileComplexity", () => {
  it("computes null density from explicit nulls", () => {
    const rows = [
      { a: 1, b: null },
      { a: null, b: null },
      { a: 3, b: 4 },
      { a: 4, b: undefined },
    ];
    const c = profileComplexity(rows);
    expect(c.maxNullDensity).toBe(0.75);
    expect(c.meanNullDensity).toBeCloseTo(0.5, 10);
  });
  it("no nulls -> zeros", () => {
    expect(profileComplexity(personRows)).toEqual({ maxNullDensity: 0, meanNullDensity: 0 });
  });
  it("empty -> zeros", () => {
    expect(profileComplexity([])).toEqual({ maxNullDensity: 0, meanNullDensity: 0 });
  });
});

function redPlan(): PipePlan {
  return { stages: [], ruleName: "low_confidence", confidence: 0.3, evidence: {} };
}
function greenPlan(): PipePlan {
  return { stages: [], ruleName: "default", confidence: 0.7, evidence: {} };
}
function profile(nRows: number): PipeProfile {
  return { nRows, nCols: 0, columnNames: [], dtypes: [], inferredDomain: null, domainConfidence: 0 };
}

describe("enforceConfidence", () => {
  it("RED at scale throws", () => {
    expect(() => enforceConfidence(redPlan(), profile(100_000))).toThrow(PipeNotConfidentError);
  });
  it("RED below threshold proceeds", () => {
    expect(() => enforceConfidence(redPlan(), profile(99_999))).not.toThrow();
  });
  it("green proceeds", () => {
    expect(() => enforceConfidence(greenPlan(), profile(100_000))).not.toThrow();
  });
});

describe("planConfig (brain wiring)", () => {
  it("confident_schema prepends infer_schema", () => {
    const rows = [
      { account_number: "A1", currency: "USD" },
      { account_number: "A2", currency: "EUR" },
    ];
    const cfg = planConfig(rows, buildDefaultRegistry(), {});
    expect(cfg.stages.map((s) => s.use)).toEqual([
      "infer_schema", "goldencheck.scan", "goldenflow.transform", "goldenmatch.dedupe",
    ]);
  });
  it("single row is pathological (drops dedupe)", () => {
    const rows = [{ first_name: "Solo", last_name: "Person", email: "s@x.co" }];
    const cfg = planConfig(rows, buildDefaultRegistry(), {});
    expect(cfg.stages.map((s) => s.use)).toEqual(["goldencheck.scan", "goldenflow.transform"]);
  });
});
