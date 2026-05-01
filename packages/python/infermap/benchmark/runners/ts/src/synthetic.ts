// Loader for benchmark/cases/synthetic/generated.json.
//
// TS does NOT implement the generator — Python is canonical (spec §7.4).
// This keeps the synthetic slice byte-identical across languages since both
// read the same committed JSON file.
import { readFileSync } from "node:fs";
import { makeFieldInfo, makeSchemaInfo, type SchemaInfo } from "infermap";
import type { CaseData } from "./types.js";

interface GeneratedField {
  name: string;
  dtype: string;
  samples: string[];
}

interface GeneratedCase {
  id: string;
  category: string;
  subcategory: string;
  tags: string[];
  expected_difficulty: string;
  source_fields: GeneratedField[];
  target_fields: GeneratedField[];
  expected: {
    mappings: Array<{ source: string; target: string }>;
    unmapped_source: string[];
    unmapped_target: string[];
  };
  applied_transforms: string[];
}

interface GeneratedFile {
  version: number;
  seed: number;
  cases: GeneratedCase[];
}

export function loadSyntheticCases(path: string): CaseData[] {
  const data = JSON.parse(readFileSync(path, "utf8")) as GeneratedFile;
  if (data.version !== 1) {
    throw new Error(`Unsupported synthetic version: ${data.version}`);
  }
  return data.cases.map((c) => ({
    id: c.id,
    category: c.category,
    subcategory: c.subcategory,
    tags: [...c.tags],
    expectedDifficulty: c.expected_difficulty,
    sourceSchema: fieldsToSchema(c.source_fields, `synthetic-source-${c.id}`),
    targetSchema: fieldsToSchema(c.target_fields, `synthetic-target-${c.id}`),
    expected: {
      mappings: c.expected.mappings.map((m) => ({ source: m.source, target: m.target })),
      unmappedSource: [...c.expected.unmapped_source],
      unmappedTarget: [...c.expected.unmapped_target],
    },
  }));
}

function fieldsToSchema(fields: GeneratedField[], sourceName: string): SchemaInfo {
  return makeSchemaInfo({
    fields: fields.map((f) =>
      makeFieldInfo({
        name: f.name,
        dtype: f.dtype,
        sampleValues: f.samples,
        // Set valueCount so ProfileScorer contributes. Without this,
        // the TS ProfileScorer abstains on every synthetic case (gate:
        // `source.valueCount === 0 || target.valueCount === 0 → null`),
        // silently measuring only 2 of 6 scorers on the synthetic slice.
        valueCount: f.samples.length,
      })
    ),
    sourceName,
  });
}
