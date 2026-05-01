import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import {
  expectedCalibrationError,
  extractPredictions,
  f1PerCase,
  meanReciprocalRank,
  topOneAccuracy,
  type MetricInput,
} from "../../src/metrics.js";

const REPO_ROOT = resolve(__dirname, "../../../../..");
const INPUTS_PATH = resolve(REPO_ROOT, "benchmark/tests/parity/metric_inputs.json");
const EXPECTED_PATH = resolve(REPO_ROOT, "benchmark/tests/parity/metric_expected.json");

const TOLERANCE = 1e-6;

interface RawInput {
  source_fields: string[];
  target_fields: string[];
  expected_mappings: Array<{ source: string; target: string }>;
  expected_unmapped_source: string[];
  expected_unmapped_target: string[];
  actual_mappings: Array<{ source: string; target: string; confidence: number }>;
  score_matrix: Record<string, Record<string, number>>;
  min_confidence: number;
}

function buildInput(raw: RawInput): MetricInput {
  return {
    sourceFields: raw.source_fields,
    targetFields: raw.target_fields,
    expectedMappings: raw.expected_mappings,
    expectedUnmappedSource: raw.expected_unmapped_source,
    expectedUnmappedTarget: raw.expected_unmapped_target,
    actualMappings: raw.actual_mappings,
    scoreMatrix: raw.score_matrix,
    minConfidence: raw.min_confidence,
  };
}

const inputs = JSON.parse(readFileSync(INPUTS_PATH, "utf8")) as {
  version: number;
  inputs: Array<{ name: string; hand_computed: boolean; input: RawInput }>;
};
const expected = JSON.parse(readFileSync(EXPECTED_PATH, "utf8")) as {
  version: number;
  expected: Array<{
    name: string;
    hand_computed: boolean;
    top1: number;
    f1_per_case: { tp: number; fp: number; fn: number };
    mrr: number;
    ece: number;
  }>;
};

const expectedMap = new Map(expected.expected.map((e) => [e.name, e]));

describe("metric parity against shared JSON corpus", () => {
  it("loads both corpus files", () => {
    expect(inputs.inputs.length).toBeGreaterThanOrEqual(5);
    expect(expected.expected.length).toBeGreaterThanOrEqual(5);
  });

  it("every input has a matching expected entry", () => {
    for (const inp of inputs.inputs) {
      expect(expectedMap.has(inp.name)).toBe(true);
    }
  });

  it("has at least 5 hand-computed entries (anti-drift floor)", () => {
    const handComputed = expected.expected.filter((e) => e.hand_computed === true);
    expect(handComputed.length).toBeGreaterThanOrEqual(5);
  });

  for (const inp of inputs.inputs) {
    it(`matches expected for ${inp.name}`, () => {
      const exp = expectedMap.get(inp.name)!;
      const mi = buildInput(inp.input);

      const actualTop1 = topOneAccuracy(mi);
      expect(Math.abs(actualTop1 - exp.top1)).toBeLessThan(TOLERANCE);

      const { tp, fp, fn } = f1PerCase(mi);
      expect(tp).toBe(exp.f1_per_case.tp);
      expect(fp).toBe(exp.f1_per_case.fp);
      expect(fn).toBe(exp.f1_per_case.fn);

      const actualMrr = meanReciprocalRank(mi);
      expect(Math.abs(actualMrr - exp.mrr)).toBeLessThan(TOLERANCE);

      const preds = extractPredictions(mi);
      const actualEce = expectedCalibrationError(preds);
      expect(Math.abs(actualEce - exp.ece)).toBeLessThan(TOLERANCE);
    });
  }
});
