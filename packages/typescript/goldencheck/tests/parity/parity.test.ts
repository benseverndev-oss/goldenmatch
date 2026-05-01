/**
 * Parity tests — validates TypeScript scan results match Python golden outputs.
 * Run `python scripts/gen_parity_goldens_js.py` to generate/update goldens.
 */

import { describe, it, expect } from "vitest";
import { existsSync, readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { TabularData } from "../../src/core/data.js";
import { scanData } from "../../src/core/engine/scanner.js";
import { applyConfidenceDowngrade } from "../../src/core/engine/confidence.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const FIXTURES_DIR = join(__dirname, "..", "..", "..", "tests", "fixtures");
const GOLDENS_DIR = join(FIXTURES_DIR, "_goldens_js");
const MANIFEST_PATH = join(FIXTURES_DIR, "parity_cases.json");

interface ParityCase {
  name: string;
  description: string;
  input: { kind: "records"; records: Record<string, unknown>[] };
  options?: { sampleSize?: number; domain?: string | null };
}

interface GoldenOutput {
  findings: Array<{
    severity: string;
    column: string;
    check: string;
    confidence: number;
  }>;
  health_grade: string;
  health_score: number;
}

describe("parity", () => {
  // Skip if manifest doesn't exist (goldens not yet generated)
  if (!existsSync(MANIFEST_PATH)) {
    it.skip("parity_cases.json not found — run scripts/gen_parity_goldens_js.py first", () => {});
    return;
  }

  const manifest: { cases: ParityCase[] } = JSON.parse(readFileSync(MANIFEST_PATH, "utf-8"));

  for (const testCase of manifest.cases) {
    it(`matches Python output for: ${testCase.name}`, () => {
      const goldenPath = join(GOLDENS_DIR, `${testCase.name}.json`);
      if (!existsSync(goldenPath)) {
        // Skip if golden not generated yet
        return;
      }

      const golden: GoldenOutput = JSON.parse(readFileSync(goldenPath, "utf-8"));
      const data = new TabularData(testCase.input.records);
      const result = scanData(data, {
        sampleSize: testCase.options?.sampleSize,
        domain: testCase.options?.domain,
      });
      const findings = applyConfidenceDowngrade(result.findings, false);

      // Compare finding identity: (column, check) pairs
      const tsFindings = findings.map((f) => ({
        column: f.column,
        check: f.check,
        severity: f.severity === 3 ? "ERROR" : f.severity === 2 ? "WARNING" : "INFO",
      }));
      const pyFindings = golden.findings.map((f) => ({
        column: f.column,
        check: f.check,
        severity: f.severity,
      }));

      // Sort both for comparison
      const sortKey = (f: { column: string; check: string }) => `${f.column}|${f.check}`;
      tsFindings.sort((a, b) => sortKey(a).localeCompare(sortKey(b)));
      pyFindings.sort((a, b) => sortKey(a).localeCompare(sortKey(b)));

      expect(tsFindings).toEqual(pyFindings);
    });
  }
});
