import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  loadCase,
  IncompleteCaseError,
  ExpectedCoverageError,
  FieldCountMismatchError,
} from "../../src/cases.js";
import type { CaseRef } from "../../src/types.js";

let tmp: string;
beforeEach(() => { tmp = mkdtempSync(join(tmpdir(), "bench-ts-cases-")); });
afterEach(() => { rmSync(tmp, { recursive: true, force: true }); });

function makeRef(relPath: string, fieldCounts: { source: number; target: number }): CaseRef {
  return {
    id: "test/dummy",
    path: relPath,
    category: "valentine",
    subcategory: "test",
    source: { name: "x", url: "x", license: "MIT", attribution: "x" },
    tags: [],
    expectedDifficulty: "easy",
    fieldCounts,
  };
}

function writeCase(
  root: string,
  sourceCsv: string,
  targetCsv: string,
  expected: unknown,
  caseMeta: unknown = {},
): string {
  const dir = join(root, "cases/valentine/test");
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "source.csv"), sourceCsv, "utf8");
  writeFileSync(join(dir, "target.csv"), targetCsv, "utf8");
  writeFileSync(join(dir, "expected.json"), JSON.stringify(expected), "utf8");
  writeFileSync(join(dir, "case.json"), JSON.stringify(caseMeta), "utf8");
  return dir;
}

describe("loadCase", () => {
  it("loads a valid case", () => {
    writeCase(
      tmp,
      "fname,email_addr\nA,a@b.co\nB,b@c.co\n",
      "first_name,email\n,\n",
      {
        mappings: [
          { source: "fname", target: "first_name" },
          { source: "email_addr", target: "email" },
        ],
        unmapped_source: [],
        unmapped_target: [],
      },
    );
    const ref = makeRef("cases/valentine/test", { source: 2, target: 2 });
    const case_ = loadCase(tmp, ref);
    expect(case_.id).toBe("test/dummy");
    expect(case_.sourceSchema.fields).toHaveLength(2);
    expect(case_.targetSchema.fields).toHaveLength(2);
    expect(case_.expected.mappings).toHaveLength(2);
    expect(case_.expected.unmappedSource).toEqual([]);
    expect(case_.expected.unmappedTarget).toEqual([]);
  });

  it("throws IncompleteCaseError on missing source.csv", () => {
    const dir = join(tmp, "cases/valentine/test");
    mkdirSync(dir, { recursive: true });
    writeFileSync(join(dir, "target.csv"), "x\n1\n", "utf8");
    writeFileSync(
      join(dir, "expected.json"),
      '{"mappings": [], "unmapped_source": [], "unmapped_target": ["x"]}',
      "utf8",
    );
    writeFileSync(join(dir, "case.json"), "{}", "utf8");

    const ref = makeRef("cases/valentine/test", { source: 0, target: 1 });
    expect(() => loadCase(tmp, ref)).toThrow(IncompleteCaseError);
  });

  it("throws ExpectedCoverageError on source coverage violation", () => {
    writeCase(
      tmp,
      "a,b\n1,2\n",
      "x\n1\n",
      { mappings: [{ source: "a", target: "x" }], unmapped_source: [], unmapped_target: [] },
    );
    const ref = makeRef("cases/valentine/test", { source: 2, target: 1 });
    expect(() => loadCase(tmp, ref)).toThrow(ExpectedCoverageError);
  });

  it("throws ExpectedCoverageError on target coverage violation", () => {
    writeCase(
      tmp,
      "a\n1\n",
      "x,y\n1,2\n",
      { mappings: [{ source: "a", target: "x" }], unmapped_source: [], unmapped_target: [] },
    );
    const ref = makeRef("cases/valentine/test", { source: 1, target: 2 });
    expect(() => loadCase(tmp, ref)).toThrow(ExpectedCoverageError);
  });

  it("throws ExpectedCoverageError on overlap", () => {
    writeCase(
      tmp,
      "a,b\n1,2\n",
      "x,y\n1,2\n",
      {
        mappings: [{ source: "a", target: "x" }, { source: "b", target: "y" }],
        unmapped_source: ["a"],
        unmapped_target: [],
      },
    );
    const ref = makeRef("cases/valentine/test", { source: 2, target: 2 });
    expect(() => loadCase(tmp, ref)).toThrow(ExpectedCoverageError);
  });

  it("throws FieldCountMismatchError on wrong source count", () => {
    writeCase(
      tmp,
      "a,b,c\n1,2,3\n",
      "x\n1\n",
      { mappings: [{ source: "a", target: "x" }], unmapped_source: ["b", "c"], unmapped_target: [] },
    );
    const ref = makeRef("cases/valentine/test", { source: 2, target: 1 });
    expect(() => loadCase(tmp, ref)).toThrow(FieldCountMismatchError);
  });

  it("throws FieldCountMismatchError on wrong target count", () => {
    writeCase(
      tmp,
      "a\n1\n",
      "x,y,z\n1,2,3\n",
      { mappings: [{ source: "a", target: "x" }], unmapped_source: [], unmapped_target: ["y", "z"] },
    );
    const ref = makeRef("cases/valentine/test", { source: 1, target: 2 });
    expect(() => loadCase(tmp, ref)).toThrow(FieldCountMismatchError);
  });

  it("throws ExpectedCoverageError when mappings key is missing", () => {
    writeCase(
      tmp,
      "a\n1\n",
      "x\n1\n",
      { unmapped_source: ["a"], unmapped_target: ["x"] },
    );
    const ref = makeRef("cases/valentine/test", { source: 1, target: 1 });
    expect(() => loadCase(tmp, ref)).toThrow(ExpectedCoverageError);
  });
});
