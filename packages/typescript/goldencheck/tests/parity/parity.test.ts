/**
 * Parity tests — validates TypeScript scan results match Python golden outputs.
 * Run `python scripts/gen_parity_goldens_js.py` (from this package dir, with the
 * goldencheck Python package importable) to (re)generate goldens. The #855
 * fixtures + goldens are regenerated on CI via `regen-855-parity-goldens.yml`.
 *
 * Asserts per-finding identity (column, check, severity) PLUS confidence (to 4
 * decimals) and affected-row counts, and FAILS (not skips) when the manifest or
 * a golden is missing — so a newly-ported profiler can't silently regress.
 *
 * NOTE: the `freshness` profiler is intentionally NOT covered here. This harness
 * round-trips each case through a temp CSV, and `pl.read_csv` defaults to
 * `try_parse_dates=False`, so date columns arrive as Utf8 and Python's
 * date-dtype-gated FreshnessProfiler never fires — while TS `dtype()` reports
 * "date" for ISO strings. Freshness parity is covered by
 * `tests/unit/profilers/freshness.test.ts` instead.
 */

import { describe, it, expect } from "vitest";
import { existsSync, readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { TabularData } from "../../src/core/data.js";
import { scanData } from "../../src/core/engine/scanner.js";
import { applyConfidenceDowngrade } from "../../src/core/engine/confidence.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
// __dirname = <pkg>/tests/parity → up two levels to <pkg>, then tests/fixtures.
const FIXTURES_DIR = join(__dirname, "..", "fixtures");
const GOLDENS_DIR = join(FIXTURES_DIR, "_goldens_js");
const MANIFEST_PATH = join(FIXTURES_DIR, "parity_cases.json");

interface ParityCase {
  name: string;
  description: string;
  input: { kind: "records"; records: Record<string, unknown>[] };
  options?: { sampleSize?: number; domain?: string | null };
}

interface GoldenFinding {
  severity: string;
  column: string;
  check: string;
  confidence: number;
  affected_rows: number;
}

interface GoldenOutput {
  findings: GoldenFinding[];
  health_grade: string;
  health_score: number;
}

const round4 = (x: number): number => Math.round(x * 1e4) / 1e4;

interface CmpFinding {
  column: string;
  check: string;
  severity: string;
  confidence: number;
  affectedRows: number;
}

const sortKey = (f: CmpFinding): string => `${f.column}|${f.check}|${f.affectedRows}`;

describe("parity", () => {
  if (!existsSync(MANIFEST_PATH)) {
    it("parity_cases.json must exist (run scripts/gen_parity_goldens_js.py)", () => {
      throw new Error(`Missing parity manifest at ${MANIFEST_PATH}`);
    });
    return;
  }

  const manifest: { cases: ParityCase[] } = JSON.parse(readFileSync(MANIFEST_PATH, "utf-8"));

  for (const testCase of manifest.cases) {
    it(`matches Python output for: ${testCase.name}`, () => {
      const goldenPath = join(GOLDENS_DIR, `${testCase.name}.json`);
      if (!existsSync(goldenPath)) {
        throw new Error(
          `Missing golden for "${testCase.name}" — run scripts/gen_parity_goldens_js.py`,
        );
      }

      const golden: GoldenOutput = JSON.parse(readFileSync(goldenPath, "utf-8"));
      const data = new TabularData(testCase.input.records);
      const result = scanData(data, {
        sampleSize: testCase.options?.sampleSize,
        domain: testCase.options?.domain,
      });
      const findings = applyConfidenceDowngrade(result.findings, false);

      const tsFindings: CmpFinding[] = findings.map((f) => ({
        column: f.column,
        check: f.check,
        severity: f.severity === 3 ? "ERROR" : f.severity === 2 ? "WARNING" : "INFO",
        confidence: round4(f.confidence),
        affectedRows: f.affectedRows,
      }));
      const pyFindings: CmpFinding[] = golden.findings.map((f) => ({
        column: f.column,
        check: f.check,
        severity: f.severity,
        confidence: round4(f.confidence),
        affectedRows: f.affected_rows,
      }));

      // Sort by a key spanning column+check+rows so multi-finding-per-column
      // cases (fuzzy, approx-duplicate) compare order-independently.
      tsFindings.sort((a, b) => sortKey(a).localeCompare(sortKey(b)));
      pyFindings.sort((a, b) => sortKey(a).localeCompare(sortKey(b)));

      expect(tsFindings).toEqual(pyFindings);
    });
  }
});
