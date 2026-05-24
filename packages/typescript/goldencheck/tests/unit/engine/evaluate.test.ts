/**
 * Tests for evaluateScan — ground-truth comparison.
 * Ported from tests/engine/test_evaluate.py.
 */
import { describe, it, expect } from "vitest";
import { evaluateScan } from "../../../src/core/engine/evaluate.js";
import { makeFinding, Severity, type Finding } from "../../../src/core/types.js";

function makeFindings(pairs: ReadonlyArray<readonly [string, string]>): Finding[] {
  return pairs.map(([col, chk]) =>
    makeFinding({ severity: Severity.WARNING, column: col, check: chk, message: "test" }),
  );
}

describe("evaluateScan", () => {
  it("perfect score: all expected present, no extras", () => {
    const findings = makeFindings([
      ["age", "null_ratio"],
      ["name", "whitespace"],
    ]);
    const expected = [
      { column: "age", check: "null_ratio" },
      { column: "name", check: "whitespace" },
    ];
    const r = evaluateScan(findings, expected);
    expect(r.precision).toBe(1.0);
    expect(r.recall).toBe(1.0);
    expect(r.f1).toBe(1.0);
    expect(r.truePositives).toBe(2);
    expect(r.falsePositives).toBe(0);
    expect(r.falseNegatives).toBe(0);
  });

  it("partial recall: scanner misses one expected", () => {
    const findings = makeFindings([["age", "null_ratio"]]);
    const expected = [
      { column: "age", check: "null_ratio" },
      { column: "name", check: "whitespace" },
    ];
    const r = evaluateScan(findings, expected);
    expect(r.precision).toBe(1.0);
    expect(r.recall).toBe(0.5);
    expect(r.truePositives).toBe(1);
    expect(r.falseNegatives).toBe(1);
    expect(r.falsePositives).toBe(0);
    expect(r.f1).toBeGreaterThan(0);
    expect(r.f1).toBeLessThan(1.0);
  });

  it("false positives: extra findings not expected", () => {
    const findings = makeFindings([
      ["age", "null_ratio"],
      ["email", "format"],
    ]);
    const expected = [{ column: "age", check: "null_ratio" }];
    const r = evaluateScan(findings, expected);
    expect(r.precision).toBe(0.5);
    expect(r.recall).toBe(1.0);
    expect(r.truePositives).toBe(1);
    expect(r.falsePositives).toBe(1);
    expect(r.falseNegatives).toBe(0);
  });

  it("empty findings, some expected: recall 0", () => {
    const r = evaluateScan([], [{ column: "age", check: "null_ratio" }]);
    expect(r.precision).toBe(1.0);
    expect(r.recall).toBe(0.0);
    expect(r.f1).toBe(0.0);
    expect(r.falseNegatives).toBe(1);
  });

  it("empty expected, some actual: precision 0", () => {
    const findings = makeFindings([["age", "null_ratio"]]);
    const r = evaluateScan(findings, []);
    expect(r.precision).toBe(0.0);
    expect(r.recall).toBe(1.0);
    expect(r.f1).toBe(0.0);
    expect(r.falsePositives).toBe(1);
  });

  it("both empty: perfect score by convention", () => {
    const r = evaluateScan([], []);
    expect(r.precision).toBe(1.0);
    expect(r.recall).toBe(1.0);
    expect(r.f1).toBe(1.0);
    expect(r.truePositives).toBe(0);
  });

  it("detail tuples are sorted", () => {
    const findings = makeFindings([
      ["b", "check2"],
      ["a", "check1"],
    ]);
    const expected = [
      { column: "a", check: "check1" },
      { column: "b", check: "check2" },
    ];
    const r = evaluateScan(findings, expected);
    expect(r.tpDetails).toEqual([
      ["a", "check1"],
      ["b", "check2"],
    ]);
  });
});
