import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { loadSyntheticCases } from "../../src/synthetic.js";

let tmp: string;
beforeEach(() => { tmp = mkdtempSync(join(tmpdir(), "bench-ts-synth-")); });
afterEach(() => { rmSync(tmp, { recursive: true, force: true }); });

function writeGenerated(data: unknown): string {
  const path = join(tmp, "generated.json");
  writeFileSync(path, JSON.stringify(data), "utf8");
  return path;
}

const sampleCase = {
  id: "synthetic/customer/easy/0",
  category: "synthetic",
  subcategory: "customer",
  tags: ["synthetic", "easy", "transform:case_change"],
  expected_difficulty: "easy",
  source_fields: [
    { name: "customerId", dtype: "integer", samples: ["1001", "1002", "1003"] },
    { name: "firstName", dtype: "string", samples: ["Alice", "Bob", "Carol"] },
  ],
  target_fields: [
    { name: "customer_id", dtype: "integer", samples: ["1001", "1002", "1003"] },
    { name: "first_name", dtype: "string", samples: ["Alice", "Bob", "Carol"] },
  ],
  expected: {
    mappings: [
      { source: "customerId", target: "customer_id" },
      { source: "firstName", target: "first_name" },
    ],
    unmapped_source: [],
    unmapped_target: [],
  },
  applied_transforms: ["case_change"],
};

describe("loadSyntheticCases", () => {
  it("loads a single case with correct shape", () => {
    const path = writeGenerated({ version: 1, seed: 42, cases: [sampleCase] });
    const cases = loadSyntheticCases(path);
    expect(cases).toHaveLength(1);
    const c = cases[0]!;
    expect(c.id).toBe("synthetic/customer/easy/0");
    expect(c.category).toBe("synthetic");
    expect(c.subcategory).toBe("customer");
    expect(c.tags).toEqual(["synthetic", "easy", "transform:case_change"]);
    expect(c.expectedDifficulty).toBe("easy");
    expect(c.sourceSchema.fields).toHaveLength(2);
    expect(c.targetSchema.fields).toHaveLength(2);
    expect(c.expected.mappings).toHaveLength(2);
    expect(c.expected.unmappedSource).toEqual([]);
    expect(c.expected.unmappedTarget).toEqual([]);
  });

  it("preserves field names, dtypes, and sample values", () => {
    const path = writeGenerated({ version: 1, seed: 42, cases: [sampleCase] });
    const [c] = loadSyntheticCases(path);
    const srcField = c!.sourceSchema.fields[0]!;
    expect(srcField.name).toBe("customerId");
    expect(srcField.dtype).toBe("integer");
    expect(srcField.sampleValues).toEqual(["1001", "1002", "1003"]);
    const tgtField = c!.targetSchema.fields[1]!;
    expect(tgtField.name).toBe("first_name");
    expect(tgtField.sampleValues).toEqual(["Alice", "Bob", "Carol"]);
  });

  it("converts snake_case JSON keys to camelCase runtime fields", () => {
    const path = writeGenerated({ version: 1, seed: 42, cases: [sampleCase] });
    const [c] = loadSyntheticCases(path);
    expect(c!.expectedDifficulty).toBe("easy");
    expect(c!.expected.unmappedSource).toEqual([]);
    expect(c!.expected.unmappedTarget).toEqual([]);
  });

  it("loads multiple cases", () => {
    const case2 = { ...sampleCase, id: "synthetic/customer/medium/1", expected_difficulty: "medium" };
    const path = writeGenerated({ version: 1, seed: 42, cases: [sampleCase, case2] });
    expect(loadSyntheticCases(path)).toHaveLength(2);
  });

  it("copies arrays so caller mutations don't affect cached data", () => {
    const path = writeGenerated({ version: 1, seed: 42, cases: [sampleCase] });
    const [c] = loadSyntheticCases(path);
    c!.tags.push("mutated");
    const [c2] = loadSyntheticCases(path);
    expect(c2!.tags).not.toContain("mutated");
  });

  it("sets valueCount === samples.length on every loaded field", () => {
    // Regression guard: ProfileScorer abstains when valueCount === 0, so
    // the loader must propagate samples.length onto valueCount or synthetic
    // cases would silently lose a scorer contributor.
    const path = writeGenerated({ version: 1, seed: 42, cases: [sampleCase] });
    const [c] = loadSyntheticCases(path);
    for (const f of c!.sourceSchema.fields) {
      expect(f.valueCount).toBe(f.sampleValues.length);
    }
    for (const f of c!.targetSchema.fields) {
      expect(f.valueCount).toBe(f.sampleValues.length);
    }
  });

  it("rejects unsupported version", () => {
    const path = writeGenerated({ version: 999, seed: 42, cases: [] });
    expect(() => loadSyntheticCases(path)).toThrow(/version/i);
  });

  it("handles empty cases array", () => {
    const path = writeGenerated({ version: 1, seed: 42, cases: [] });
    expect(loadSyntheticCases(path)).toEqual([]);
  });

  it("handles a case with unmapped source fields", () => {
    const caseWithUnmapped = {
      ...sampleCase,
      source_fields: [
        ...sampleCase.source_fields,
        { name: "distractor", dtype: "string", samples: ["xyz"] },
      ],
      expected: {
        mappings: sampleCase.expected.mappings,
        unmapped_source: ["distractor"],
        unmapped_target: [],
      },
    };
    const path = writeGenerated({ version: 1, seed: 42, cases: [caseWithUnmapped] });
    const [c] = loadSyntheticCases(path);
    expect(c!.expected.unmappedSource).toEqual(["distractor"]);
    expect(c!.sourceSchema.fields).toHaveLength(3);
  });
});

describe("loadSyntheticCases against the real committed file", () => {
  const REPO_ROOT = resolve(__dirname, "../../../../..");
  const GENERATED_PATH = resolve(REPO_ROOT, "benchmark/cases/synthetic/generated.json");

  it("loads the real generated.json with 80 cases", () => {
    const cases = loadSyntheticCases(GENERATED_PATH);
    expect(cases.length).toBe(80);
    expect(cases[0]!.sourceSchema.fields.length).toBeGreaterThan(0);
    expect(cases[0]!.targetSchema.fields.length).toBeGreaterThan(0);
    for (const c of cases) {
      expect(["easy", "medium", "hard"]).toContain(c.expectedDifficulty);
    }
  });
});
