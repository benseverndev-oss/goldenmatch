// Parity test suite. Loads tests/fixtures/parity_cases.json from the repo
// root, runs the TypeScript engine on each case, and compares against the
// Python-generated golden in tests/fixtures/_goldens/<name>.json.
//
// Drift in confidence scores is tolerated up to 4 decimal places (matches
// the Python generator's rounding). Mapping *identity* (which source maps
// to which target) must be bit-exact.
//
// If this suite fails, the most likely culprits are:
//   1. Dtype inference divergence (polars vs JS heuristic) affecting ProfileScorer
//   2. Jaro-Winkler output drift (rapidfuzz vs vendored)
//   3. Regex dialect differences in PatternTypeScorer

import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { MapEngine } from "../../src/core/engine.js";
import { inferSchemaFromRecords } from "../../src/core/providers/in-memory.js";
import type { MapResult } from "../../src/core/types.js";

const REPO_ROOT = resolve(__dirname, "../../../..");
const MANIFEST_PATH = resolve(REPO_ROOT, "tests/fixtures/parity_cases.json");
const GOLDENS_DIR = resolve(REPO_ROOT, "tests/fixtures/_goldens");

const CONFIDENCE_PRECISION = 4;

interface InputSpec {
  kind: "records" | "csv";
  records?: Array<Record<string, unknown>>;
  path?: string;
}

interface ParityCase {
  name: string;
  description: string;
  source: InputSpec;
  target: InputSpec;
  min_confidence: number;
}

interface Golden {
  min_confidence: number;
  mappings: Array<{ source: string; target: string; confidence: number }>;
  unmapped_source: string[];
  unmapped_target: string[];
}

function loadManifest(): ParityCase[] {
  const raw = JSON.parse(readFileSync(MANIFEST_PATH, "utf8"));
  return raw.cases as ParityCase[];
}

function loadGolden(name: string): Golden {
  return JSON.parse(readFileSync(resolve(GOLDENS_DIR, `${name}.json`), "utf8"));
}

function round(n: number, precision: number): number {
  const f = 10 ** precision;
  return Math.round(n * f) / f;
}

function normalizeResult(result: MapResult, minConfidence: number): Golden {
  const mappings = result.mappings
    .map((m) => ({
      source: m.source,
      target: m.target,
      confidence: round(m.confidence, CONFIDENCE_PRECISION),
    }))
    .sort((a, b) =>
      a.source === b.source
        ? a.target.localeCompare(b.target)
        : a.source.localeCompare(b.source)
    );
  return {
    min_confidence: minConfidence,
    mappings,
    unmapped_source: [...result.unmappedSource].sort(),
    unmapped_target: [...result.unmappedTarget].sort(),
  };
}

function schemaFromSpec(spec: InputSpec) {
  if (spec.kind !== "records" || !spec.records) {
    throw new Error(
      `parity test only supports kind:"records" inputs for now; got ${spec.kind}`
    );
  }
  return inferSchemaFromRecords(spec.records);
}

describe("parity (TS vs Python golden)", () => {
  const cases = loadManifest();
  expect(cases.length).toBeGreaterThan(0);

  for (const kase of cases) {
    it(`${kase.name}: ${kase.description}`, () => {
      const golden = loadGolden(kase.name);
      const src = schemaFromSpec(kase.source);
      const tgt = schemaFromSpec(kase.target);

      const engine = new MapEngine({ minConfidence: kase.min_confidence });
      const result = engine.mapSchemas(src, tgt);
      const actual = normalizeResult(result, kase.min_confidence);

      // Mapping pairs must match exactly (order + identity).
      expect(actual.mappings.map((m) => [m.source, m.target])).toEqual(
        golden.mappings.map((m) => [m.source, m.target])
      );

      // Confidence scores must match to the declared precision.
      for (let i = 0; i < actual.mappings.length; i++) {
        expect(
          Math.abs(
            actual.mappings[i]!.confidence - golden.mappings[i]!.confidence
          )
        ).toBeLessThan(1e-4);
      }

      // Unmapped lists must match exactly (pre-sorted).
      expect(actual.unmapped_source).toEqual(golden.unmapped_source);
      expect(actual.unmapped_target).toEqual(golden.unmapped_target);
    });
  }
});
