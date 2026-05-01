import { describe, it, expect } from "vitest";
import { makeFieldInfo, makeSchemaInfo, type MapResult } from "infermap";
import {
  FailureBudgetExceededError,
  abortIfOverBudget,
  runCases,
} from "../../src/runner.js";
import type { CaseData } from "../../src/types.js";

function makeCase(opts: {
  caseId: string;
  srcNames: string[];
  tgtNames: string[];
  mappings: Array<[string, string]>;
  tags?: string[];
  category?: string;
  difficulty?: string;
}): CaseData {
  const srcSet = new Set(opts.mappings.map(([s]) => s));
  const tgtSet = new Set(opts.mappings.map(([, t]) => t));
  return {
    id: opts.caseId,
    category: opts.category ?? "valentine",
    subcategory: "test",
    tags: opts.tags ?? [],
    expectedDifficulty: opts.difficulty ?? "easy",
    sourceSchema: makeSchemaInfo({
      fields: opts.srcNames.map((n) => makeFieldInfo({ name: n })),
    }),
    targetSchema: makeSchemaInfo({
      fields: opts.tgtNames.map((n) => makeFieldInfo({ name: n })),
    }),
    expected: {
      mappings: opts.mappings.map(([source, target]) => ({ source, target })),
      unmappedSource: opts.srcNames.filter((n) => !srcSet.has(n)),
      unmappedTarget: opts.tgtNames.filter((n) => !tgtSet.has(n)),
    },
  };
}

class FakeEngine {
  readonly minConfidence = 0.3;
  constructor(private handler: (src: any, tgt: any) => MapResult) {}
  mapSchemas(src: any, tgt: any): MapResult {
    return this.handler(src, tgt);
  }
}

describe("abortIfOverBudget", () => {
  it("under budget is a no-op", () => {
    expect(() => abortIfOverBudget(5, 100, 0.10)).not.toThrow();
    expect(() => abortIfOverBudget(10, 100, 0.10)).not.toThrow();
  });

  it("over budget throws", () => {
    expect(() => abortIfOverBudget(15, 100, 0.10)).toThrow(FailureBudgetExceededError);
  });

  it("error message contains counts and budget", () => {
    try {
      abortIfOverBudget(15, 100, 0.10);
    } catch (e) {
      const msg = String((e as Error).message);
      expect(msg).toContain("15");
      expect(msg).toContain("100");
    }
  });

  it("zero total does not throw", () => {
    expect(() => abortIfOverBudget(0, 0, 0.10)).not.toThrow();
  });
});

describe("runCases — happy path", () => {
  it("runs all cases with the real engine", () => {
    const cases = [
      makeCase({ caseId: "a", srcNames: ["first_name"], tgtNames: ["first_name"], mappings: [["first_name", "first_name"]] }),
      makeCase({ caseId: "b", srcNames: ["email"], tgtNames: ["email"], mappings: [["email", "email"]] }),
    ];
    const results = runCases(cases);
    expect(results).toHaveLength(2);
    expect(results.every((r) => !r.failed)).toBe(true);
  });

  it("carries case metadata into CaseResult", () => {
    const cases = [
      makeCase({
        caseId: "x/y/z",
        srcNames: ["a"], tgtNames: ["a"], mappings: [["a", "a"]],
        tags: ["alias_dominant", "small"],
        category: "synthetic",
        difficulty: "hard",
      }),
    ];
    const results = runCases(cases);
    expect(results[0]!.caseId).toBe("x/y/z");
    expect(results[0]!.category).toBe("synthetic");
    expect(results[0]!.difficulty).toBe("hard");
    expect(results[0]!.tags).toEqual(["alias_dominant", "small"]);
  });

  it("empty case list returns empty", () => {
    expect(runCases([])).toEqual([]);
  });
});

describe("runCases — failure handling", () => {
  it("engine exception records failed case and continues", () => {
    const cases = [
      makeCase({ caseId: "a", srcNames: ["x"], tgtNames: ["x"], mappings: [["x", "x"]] }),
    ];
    const engineFactory = () =>
      new FakeEngine((): MapResult => {
        throw new Error("intentional test failure");
      }) as any;

    const results = runCases(cases, { failureBudget: 1.0 }, engineFactory);
    expect(results).toHaveLength(1);
    expect(results[0]!.failed).toBe(true);
    expect(results[0]!.failureReason).toContain("intentional test failure");
    expect(results[0]!.f1).toBe(0);
    expect(results[0]!.tp).toBe(0);
    expect(results[0]!.fn).toBe(1);
  });

  it("failure budget exceeded aborts", () => {
    const cases = Array.from({ length: 11 }, (_, i) =>
      makeCase({ caseId: `c${i}`, srcNames: ["a"], tgtNames: ["a"], mappings: [["a", "a"]] }),
    );
    const engineFactory = () =>
      new FakeEngine((): MapResult => {
        throw new Error("boom");
      }) as any;

    expect(() => runCases(cases, { failureBudget: 0.10 }, engineFactory)).toThrow(FailureBudgetExceededError);
  });
});
