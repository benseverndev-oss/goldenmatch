import { readFileSync } from "node:fs";
import { join } from "node:path";
import { inferSchemaFromCsvText } from "infermap";
import type { CaseData, CaseRef, Expected } from "./types.js";

export class IncompleteCaseError extends Error {
  constructor(message: string) { super(message); this.name = "IncompleteCaseError"; }
}
export class ExpectedCoverageError extends Error {
  constructor(message: string) { super(message); this.name = "ExpectedCoverageError"; }
}
export class FieldCountMismatchError extends Error {
  constructor(message: string) { super(message); this.name = "FieldCountMismatchError"; }
}

const REQUIRED_FILES = ["source.csv", "target.csv", "expected.json", "case.json"] as const;

export function loadCase(benchmarkRoot: string, ref: CaseRef): CaseData {
  const caseDir = join(benchmarkRoot, ref.path);
  for (const file of REQUIRED_FILES) {
    try {
      readFileSync(join(caseDir, file));
    } catch {
      throw new IncompleteCaseError(`cases/${ref.id}: missing ${file}`);
    }
  }

  const srcText = readFileSync(join(caseDir, "source.csv"), "utf8");
  const tgtText = readFileSync(join(caseDir, "target.csv"), "utf8");
  const srcSchema = inferSchemaFromCsvText(srcText);
  const tgtSchema = inferSchemaFromCsvText(tgtText);

  if (srcSchema.fields.length !== ref.fieldCounts.source) {
    throw new FieldCountMismatchError(
      `cases/${ref.id}: manifest says source has ${ref.fieldCounts.source} fields, CSV has ${srcSchema.fields.length}`
    );
  }
  if (tgtSchema.fields.length !== ref.fieldCounts.target) {
    throw new FieldCountMismatchError(
      `cases/${ref.id}: manifest says target has ${ref.fieldCounts.target} fields, CSV has ${tgtSchema.fields.length}`
    );
  }

  const expectedRaw: unknown = JSON.parse(readFileSync(join(caseDir, "expected.json"), "utf8"));
  const expected = parseExpected(
    ref.id,
    expectedRaw,
    srcSchema.fields.map((f) => f.name),
    tgtSchema.fields.map((f) => f.name),
  );

  return {
    id: ref.id,
    category: ref.category,
    subcategory: ref.subcategory,
    tags: [...ref.tags],
    expectedDifficulty: ref.expectedDifficulty,
    sourceSchema: srcSchema,
    targetSchema: tgtSchema,
    expected,
  };
}

function parseExpected(
  caseId: string,
  raw: unknown,
  sourceNames: string[],
  targetNames: string[],
): Expected {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) {
    throw new ExpectedCoverageError(`cases/${caseId}: expected.json must be an object`);
  }
  const obj = raw as Record<string, unknown>;
  for (const key of ["mappings", "unmapped_source", "unmapped_target"]) {
    if (!(key in obj)) {
      throw new ExpectedCoverageError(`cases/${caseId}: expected.json missing '${key}'`);
    }
  }
  if (!Array.isArray(obj["mappings"])) {
    throw new ExpectedCoverageError(`cases/${caseId}: expected.json 'mappings' must be a list`);
  }
  const mappings: Array<{ source: string; target: string }> = [];
  for (const m of obj["mappings"] as unknown[]) {
    if (typeof m !== "object" || m === null) {
      throw new ExpectedCoverageError(`cases/${caseId}: each mapping must have 'source' and 'target'`);
    }
    const mm = m as Record<string, unknown>;
    if (typeof mm["source"] !== "string" || typeof mm["target"] !== "string") {
      throw new ExpectedCoverageError(`cases/${caseId}: each mapping must have 'source' and 'target'`);
    }
    mappings.push({ source: mm["source"], target: mm["target"] });
  }
  const unmappedSource = obj["unmapped_source"] as string[];
  const unmappedTarget = obj["unmapped_target"] as string[];

  const srcSet = new Set(sourceNames);
  const tgtSet = new Set(targetNames);
  const mappedSrc = new Set(mappings.map((m) => m.source));
  const mappedTgt = new Set(mappings.map((m) => m.target));
  const unmappedSrcSet = new Set(unmappedSource);
  const unmappedTgtSet = new Set(unmappedTarget);

  for (const name of srcSet) {
    if (!mappedSrc.has(name) && !unmappedSrcSet.has(name)) {
      throw new ExpectedCoverageError(`cases/${caseId}: source column '${name}' not categorized`);
    }
  }
  for (const name of tgtSet) {
    if (!mappedTgt.has(name) && !unmappedTgtSet.has(name)) {
      throw new ExpectedCoverageError(`cases/${caseId}: target column '${name}' not categorized`);
    }
  }
  for (const name of mappedSrc) {
    if (unmappedSrcSet.has(name)) {
      throw new ExpectedCoverageError(`cases/${caseId}: source column '${name}' in both mapped and unmapped`);
    }
  }
  for (const name of mappedTgt) {
    if (unmappedTgtSet.has(name)) {
      throw new ExpectedCoverageError(`cases/${caseId}: target column '${name}' in both mapped and unmapped`);
    }
  }

  return { mappings, unmappedSource, unmappedTarget };
}
