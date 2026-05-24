/**
 * Cross-language parity test.
 *
 * Asserts that the TS goldenpipe produces the same stable, skew-robust
 * invariants as the Python sibling on the same CSV fixtures. The goldens live
 * in tests/fixtures/pipe_parity.json, emitted by
 * packages/python/goldenpipe/scripts/emit_ts_parity_fixtures.py.
 *
 * Because the TS and Python siblings are version-skewed, we deliberately assert
 * only invariants that survive the skew:
 *   - pipe-level `status`
 *   - `input_rows`
 *   - per-stage status + ordered stage/skip sequence
 *   - final golden / unique counts
 *
 * Documented divergences:
 *   - Python records `golden_count: null` when no golden records were produced
 *     (the artifact is absent / an empty DataFrame). TS always exposes a
 *     (possibly empty) array, so we normalize Python `null` -> 0 before
 *     comparing.
 *   - The Python `goldencheck.scan` adapter calls `scan_file(path)`, so the
 *     in-memory `run_df` path FAILS that stage. The TS adapter scans rows
 *     directly and succeeds in either path. We therefore drive the TS side via
 *     the same in-memory rows parsed from each case's `input_csv` and assert
 *     against the file-based Python goldens (whose scan stage succeeds).
 */

import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { runDf, parseCsv } from "../../src/node/index.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const FIXTURE = join(__dirname, "..", "fixtures", "pipe_parity.json");

interface StageGolden {
  name: string;
  status: string;
}
interface CaseGolden {
  id: string;
  input_csv: string;
  status: string;
  input_rows: number;
  stages: StageGolden[];
  skipped: string[];
  golden_count: number | null;
  unique_count: number | null;
}
interface ParityGoldens {
  python_version: string;
  cases: CaseGolden[];
}

const goldens = JSON.parse(readFileSync(FIXTURE, "utf8")) as ParityGoldens;

function arrLen(v: unknown): number {
  return Array.isArray(v) ? v.length : 0;
}

describe("goldenpipe cross-language parity", () => {
  it("loaded at least one parity case", () => {
    expect(goldens.cases.length).toBeGreaterThan(0);
  });

  for (const gold of goldens.cases) {
    it(`case '${gold.id}' matches Python invariants`, async () => {
      const rows = parseCsv(gold.input_csv);
      const result = await runDf(rows);

      // Pipe-level status + input rows.
      expect(result.status).toBe(gold.status);
      expect(result.inputRows).toBe(gold.input_rows);

      // Per-stage status + ordered run/skip sequence.
      const tsStages = Object.entries(result.stages).map(([name, sr]) => ({
        name,
        status: sr.status,
      }));
      expect(tsStages).toEqual(gold.stages);
      expect(result.skipped).toEqual(gold.skipped);

      // Final golden / unique counts. Python null -> 0 (no golden records).
      const expectedGolden = gold.golden_count ?? 0;
      const expectedUnique = gold.unique_count ?? 0;
      expect(arrLen(result.artifacts["golden"])).toBe(expectedGolden);
      expect(arrLen(result.artifacts["unique"])).toBe(expectedUnique);
    });
  }
});
