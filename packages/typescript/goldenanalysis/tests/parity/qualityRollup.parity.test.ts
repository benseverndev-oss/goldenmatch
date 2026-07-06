/**
 * Cross-surface lock for the `quality.rollup` analyzer — parity with the Python
 * reference (`test_quality_rollup_parity.py`) against the SAME fixture.
 *
 * `quality_rollup_result.json` is a byte-identical copy of
 * `packages/python/goldenanalysis/tests/fixtures/quality_rollup_result.json`. Each case
 * carries its `input` artifacts (findings/manifest — plain JSON dicts) and the
 * Python-locked `expected` {metrics, tables}. Raw `toEqual` (no engine-specific fields).
 *
 * Locks the drift-prone bits: `Counter.most_common` tie ordering (count desc, ties in
 * first-appearance order), unknown-check fallback, null-column filtering, metric array
 * order. The `quality.score` health-score path is out of scope (external profile
 * method); covered by per-surface unit tests.
 */

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { QualityRollupAnalyzer } from "../../src/core/analyzers/qualityRollup.js";
import type { AnalyzerInput } from "../../src/core/types.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const FIXTURE = join(__dirname, "..", "fixtures", "quality_rollup_result.json");

interface Case {
  input: Record<string, unknown>;
  expected: unknown;
}

describe("parity: quality.rollup vs python", () => {
  it("analyzer result matches the python-locked fixture exactly", () => {
    const cases = JSON.parse(readFileSync(FIXTURE, "utf-8")) as Record<string, Case>;
    for (const [name, { input, expected }] of Object.entries(cases)) {
      const inp: AnalyzerInput = { dataset: "d", artifacts: input };
      const r = new QualityRollupAnalyzer().run(inp);
      expect({ metrics: r.metrics, tables: r.tables }, name).toEqual(expected);
    }
  });
});
