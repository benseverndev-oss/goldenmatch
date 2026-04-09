import { describe, it, expect } from "vitest";
import {
  expectedCalibrationError,
  extractPredictions,
  f1PerCase,
  macroMean,
  meanReciprocalRank,
  microF1,
  topOneAccuracy,
  type MetricInput,
  type Prediction,
} from "../../src/metrics.js";

function makeInput(opts: {
  sourceFields: string[];
  targetFields?: string[];
  expectedMappings?: Array<[string, string]>;
  expectedUnmappedSrc?: string[];
  expectedUnmappedTgt?: string[];
  actualMappings?: Array<[string, string, number]>;
  scoreMatrix?: Record<string, Record<string, number>>;
}): MetricInput {
  return {
    sourceFields: opts.sourceFields,
    targetFields: opts.targetFields ?? [],
    expectedMappings: (opts.expectedMappings ?? []).map(([source, target]) => ({ source, target })),
    expectedUnmappedSource: opts.expectedUnmappedSrc ?? [],
    expectedUnmappedTarget: opts.expectedUnmappedTgt ?? [],
    actualMappings: (opts.actualMappings ?? []).map(([source, target, confidence]) => ({
      source, target, confidence,
    })),
    scoreMatrix: opts.scoreMatrix ?? {},
    minConfidence: 0.3,
  };
}

describe("topOneAccuracy", () => {
  it("perfect prediction returns 1", () => {
    const inp = makeInput({
      sourceFields: ["a", "b"],
      targetFields: ["A", "B"],
      expectedMappings: [["a", "A"], ["b", "B"]],
      actualMappings: [["a", "A", 0.9], ["b", "B", 0.9]],
    });
    expect(topOneAccuracy(inp)).toBe(1);
  });

  it("all wrong returns 0", () => {
    const inp = makeInput({
      sourceFields: ["a", "b"],
      expectedMappings: [["a", "A"], ["b", "B"]],
      actualMappings: [["a", "B", 0.9], ["b", "A", 0.9]],
    });
    expect(topOneAccuracy(inp)).toBe(0);
  });

  it("correctly unmapped source counts as hit", () => {
    const inp = makeInput({
      sourceFields: ["a", "b"],
      expectedMappings: [["a", "A"]],
      expectedUnmappedSrc: ["b"],
      actualMappings: [["a", "A", 0.9]],
    });
    expect(topOneAccuracy(inp)).toBe(1);
  });

  it("incorrectly mapped source counts as miss", () => {
    const inp = makeInput({
      sourceFields: ["a", "b"],
      expectedMappings: [["a", "A"]],
      expectedUnmappedSrc: ["b"],
      actualMappings: [["a", "A", 0.9], ["b", "A", 0.5]],
    });
    expect(topOneAccuracy(inp)).toBe(0.5);
  });

  it("expected mapped but missing from predicted counts as miss", () => {
    const inp = makeInput({
      sourceFields: ["a"],
      expectedMappings: [["a", "A"]],
      actualMappings: [],
    });
    expect(topOneAccuracy(inp)).toBe(0);
  });

  it("empty source fields returns 0", () => {
    const inp = makeInput({ sourceFields: [] });
    expect(topOneAccuracy(inp)).toBe(0);
  });
});

describe("f1PerCase + microF1", () => {
  it("perfect case", () => {
    const inp = makeInput({
      sourceFields: ["a", "b"],
      expectedMappings: [["a", "A"], ["b", "B"]],
      actualMappings: [["a", "A", 0.9], ["b", "B", 0.9]],
    });
    expect(f1PerCase(inp)).toEqual({ tp: 2, fp: 0, fn: 0 });
    expect(microF1([{ tp: 2, fp: 0, fn: 0 }])).toBe(1);
  });

  it("zero case", () => {
    const inp = makeInput({
      sourceFields: ["a"],
      expectedMappings: [["a", "A"]],
      actualMappings: [],
    });
    expect(f1PerCase(inp)).toEqual({ tp: 0, fp: 0, fn: 1 });
    expect(microF1([{ tp: 0, fp: 0, fn: 1 }])).toBe(0);
  });

  it("empty expected + empty predicted is perfect negative", () => {
    const inp = makeInput({
      sourceFields: ["a"],
      expectedUnmappedSrc: ["a"],
      expectedUnmappedTgt: ["A"],
      actualMappings: [],
    });
    const counts = f1PerCase(inp);
    expect(counts).toEqual({ tp: 0, fp: 0, fn: 0 });
    expect(microF1([counts])).toBe(1);
  });

  it("mixed 3-of-4 with 1 false positive", () => {
    const inp = makeInput({
      sourceFields: ["a", "b", "c", "d"],
      expectedMappings: [["a", "A"], ["b", "B"], ["c", "C"], ["d", "D"]],
      actualMappings: [["a", "A", 0.9], ["b", "B", 0.9], ["c", "C", 0.9], ["d", "X", 0.6]],
    });
    expect(f1PerCase(inp)).toEqual({ tp: 3, fp: 1, fn: 1 });
    expect(microF1([{ tp: 3, fp: 1, fn: 1 }])).toBe(0.75);
  });

  it("micro F1 sums across cases", () => {
    expect(microF1([{ tp: 2, fp: 0, fn: 0 }, { tp: 1, fp: 1, fn: 1 }])).toBe(0.75);
  });

  it("deduplicates predicted pairs", () => {
    const inp = makeInput({
      sourceFields: ["a"],
      expectedMappings: [["a", "A"]],
      actualMappings: [["a", "A", 0.9], ["a", "A", 0.8]],
    });
    expect(f1PerCase(inp)).toEqual({ tp: 1, fp: 0, fn: 0 });
  });
});

describe("meanReciprocalRank", () => {
  it("rank 1 returns 1", () => {
    const inp = makeInput({
      sourceFields: ["a"],
      expectedMappings: [["a", "A"]],
      scoreMatrix: { a: { A: 0.9, B: 0.5, C: 0.3 } },
    });
    expect(meanReciprocalRank(inp)).toBe(1);
  });

  it("rank 3 returns 1/3", () => {
    const inp = makeInput({
      sourceFields: ["a"],
      expectedMappings: [["a", "C"]],
      scoreMatrix: { a: { A: 0.9, B: 0.5, C: 0.3 } },
    });
    expect(Math.abs(meanReciprocalRank(inp) - 1 / 3)).toBeLessThan(1e-9);
  });

  it("correct target missing from row returns 0 for that source", () => {
    const inp = makeInput({
      sourceFields: ["a"],
      expectedMappings: [["a", "B"]],
      scoreMatrix: { a: { A: 0.5 } },
    });
    expect(meanReciprocalRank(inp)).toBe(0);
  });

  it("source absent from score matrix returns 0 for that source", () => {
    const inp = makeInput({
      sourceFields: ["a"],
      expectedMappings: [["a", "A"]],
      scoreMatrix: {},
    });
    expect(meanReciprocalRank(inp)).toBe(0);
  });

  it("empty expected returns 1", () => {
    const inp = makeInput({
      sourceFields: ["a"],
      expectedUnmappedSrc: ["a"],
    });
    expect(meanReciprocalRank(inp)).toBe(1);
  });

  it("deterministic tie break by target name ascending", () => {
    const inp = makeInput({
      sourceFields: ["a"],
      expectedMappings: [["a", "B"]],
      scoreMatrix: { a: { A: 0.5, B: 0.5, C: 0.5 } },
    });
    expect(meanReciprocalRank(inp)).toBe(0.5);
  });

  it("averages across multiple sources", () => {
    const inp = makeInput({
      sourceFields: ["a", "b"],
      expectedMappings: [["a", "A"], ["b", "B"]],
      scoreMatrix: {
        a: { A: 0.9, B: 0.1 },
        b: { A: 0.9, B: 0.1 },
      },
    });
    expect(meanReciprocalRank(inp)).toBe(0.75);
  });
});

describe("expectedCalibrationError", () => {
  it("perfect calibration returns near 0", () => {
    const preds: Prediction[] = [
      ...Array(9).fill({ confidence: 0.9, correct: true }),
      { confidence: 0.9, correct: false },
    ];
    expect(expectedCalibrationError(preds, 10)).toBeLessThan(0.01);
  });

  it("overconfident predictions have high ECE", () => {
    const preds: Prediction[] = [
      ...Array(5).fill({ confidence: 0.99, correct: true }),
      ...Array(5).fill({ confidence: 0.99, correct: false }),
    ];
    const ece = expectedCalibrationError(preds, 10);
    expect(ece).toBeGreaterThan(0.45);
    expect(ece).toBeLessThan(0.55);
  });

  it("empty input returns 0", () => {
    expect(expectedCalibrationError([])).toBe(0);
  });

  it("single prediction in one bin", () => {
    const ece = expectedCalibrationError([{ confidence: 0.8, correct: true }]);
    expect(Math.abs(ece - 0.2)).toBeLessThan(1e-9);
  });

  it("multi bin weighted", () => {
    const preds: Prediction[] = [
      ...Array(4).fill({ confidence: 0.15, correct: true }),
      ...Array(4).fill({ confidence: 0.15, correct: false }),
      ...Array(2).fill({ confidence: 0.95, correct: true }),
    ];
    expect(Math.abs(expectedCalibrationError(preds, 10) - 0.29)).toBeLessThan(1e-9);
  });
});

describe("helpers", () => {
  it("macroMean empty is 0", () => {
    expect(macroMean([])).toBe(0);
  });

  it("macroMean averages", () => {
    expect(Math.abs(macroMean([0.5, 0.5, 1.0]) - 2 / 3)).toBeLessThan(1e-9);
  });

  it("extractPredictions maps correctness", () => {
    const inp = makeInput({
      sourceFields: ["a", "b"],
      expectedMappings: [["a", "A"], ["b", "B"]],
      actualMappings: [["a", "A", 0.9], ["b", "X", 0.5]],
    });
    const preds = extractPredictions(inp);
    expect(preds).toHaveLength(2);
    expect(preds[0]!.confidence).toBe(0.9);
    expect(preds[0]!.correct).toBe(true);
    expect(preds[1]!.confidence).toBe(0.5);
    expect(preds[1]!.correct).toBe(false);
  });
});
