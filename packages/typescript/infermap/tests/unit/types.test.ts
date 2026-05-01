import { describe, it, expect } from "vitest";
import {
  VALID_DTYPES,
  makeFieldInfo,
  makeSchemaInfo,
  makeScorerResult,
  clampScore,
  mapResultToReport,
  mapResultToJson,
} from "../../src/core/types.js";

describe("types", () => {
  it("coerces invalid dtype to string", () => {
    const f = makeFieldInfo({ name: "id", dtype: "nonsense" });
    expect(f.dtype).toBe("string");
  });

  it("accepts every valid dtype", () => {
    for (const d of VALID_DTYPES) {
      expect(makeFieldInfo({ name: "x", dtype: d }).dtype).toBe(d);
    }
  });

  it("fills schema defaults", () => {
    const s = makeSchemaInfo({ fields: [] });
    expect(s.sourceName).toBe("");
    expect(s.requiredFields).toEqual([]);
  });

  it("clamps scores to [0, 1]", () => {
    expect(clampScore(-1)).toBe(0);
    expect(clampScore(2)).toBe(1);
    expect(clampScore(0.5)).toBe(0.5);
    expect(clampScore(NaN)).toBe(0);
  });

  it("clamps via makeScorerResult", () => {
    expect(makeScorerResult(1.5, "x").score).toBe(1);
    expect(makeScorerResult(-0.1, "x").score).toBe(0);
  });

  it("reports with rounding", () => {
    const rep = mapResultToReport({
      mappings: [
        {
          source: "a",
          target: "b",
          confidence: 0.123456,
          breakdown: {
            ExactScorer: { score: 0.987654, reasoning: "r" },
          },
          reasoning: "top",
        },
      ],
      unmappedSource: ["x"],
      unmappedTarget: ["y"],
      warnings: [],
      metadata: {},
    });
    expect(rep.mappings[0]!.confidence).toBe(0.123);
    expect(rep.mappings[0]!.breakdown["ExactScorer"]!.score).toBe(0.988);
    expect(rep.unmapped_source).toEqual(["x"]);
  });

  it("emits stable JSON", () => {
    const json = mapResultToJson({
      mappings: [],
      unmappedSource: [],
      unmappedTarget: [],
      warnings: [],
      metadata: {},
    });
    expect(JSON.parse(json)).toEqual({
      mappings: [],
      unmapped_source: [],
      unmapped_target: [],
      warnings: [],
    });
  });
});
